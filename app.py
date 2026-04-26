import streamlit as st
import google.genai as genai
from docx import Document
import requests
from bs4 import BeautifulSoup
import re
import urllib.parse
from urllib.parse import quote
import os

# --- 1. CONFIGURAZIONE PAGINA E CSS ---
# Impostiamo il layout largo e lo stile per una lettura ottimale su smartphone
st.set_page_config(page_title="Assistente Liturgico", page_icon="📖", layout="wide")

st.markdown("""
<style>
/* Migliora la leggibilità del testo e dei blocchi di codice su mobile */
.stMarkdown p, .stMarkdown li, .stMarkdown span, code, pre {
    white-space: pre-wrap !important;
    word-break: break-word !important;
    overflow-wrap: break-word !important;
    font-size: 1.1rem !important;
    font-family: 'Inconsolata', 'Tahoma', 'Times New Roman', serif !important;
}
/* Evita che le scritte tecniche della sidebar vadano a capo in modo strano */
[data-testid="stSidebarNav"] span { white-space: nowrap !important; }
</style>
""", unsafe_allow_html=True)

# --- 2. RECUPERO API KEY E CONFIGURAZIONE MODELLO ---
try:
    api_key = st.secrets["GEMINI_API_KEY"]
    client = genai.Client(api_key=api_key)
    # Utilizziamo la versione 2.5 Flash come richiesto
    NOME_MODELLO = "gemini-2.5-flash" 
    session = requests.Session()
    # User-Agent necessario per non essere bloccati dai siti (Villapizzone/Barzillai)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
except Exception as e:
    st.error("Errore nella configurazione dell'API Key nei Secrets.")
    st.stop()

# --- 3. LOGICA BIBLICA E MATRIOSKE ---
def analizza_intervallo(testo):
    """Estrae libro, capitolo e versetti da una stringa."""
    m = re.search(r'(Mt|Mc|Lc|Gv)\s*(\d+)\s*,\s*(\d+)(?:\s*-\s*(\d+))?', testo, re.IGNORECASE)
    if m:
        lib, cap, ini, fin = m.groups()
        return {"l": lib.capitalize(), "c": int(cap), "s": int(ini), "e": int(fin if fin else ini)}
    return None

def sono_sovrapposti(r1, r2):
    """Verifica se due riferimenti biblici si sovrappongono."""
    if r1['l'] != r2['l'] or r1['c'] != r2['c']: return False
    return max(r1['s'], r2['s']) <= min(r1['e'], r2['e'])

def espandi_matrioska(brano):
    """Crea una lista di sotto-brani per cercare corrispondenze parziali."""
    m = analizza_intervallo(brano)
    if not m: return [brano]
    res = [brano]
    # Aggiunge i singoli versetti per una ricerca più capillare
    for i in range(m['s'], m['e'] + 1):
        res.append(f"{m['l']} {m['c']},{i}")
    return res

# --- 4. FUNZIONI DI RICERCA COMMENTI ---

def cerca_villapizzone(brani_list, session):
    """Cerca commenti (Audio/PDF) sul sito dei Gesuiti di Villapizzone."""
    validi = []
    url = "https://www.gesuiti-villapizzone.it/sito/van.html"
    try:
        res = session.get(url, timeout=15)
        res.encoding = 'utf-8'
        soup = BeautifulSoup(res.text, 'html.parser')
        links = soup.find_all('a')
        
        for i, a in enumerate(links):
            testo = a.get_text().strip()
            if any(lib in testo for lib in ["Mt", "Mc", "Lc", "Gv"]):
                ref_trovato = analizza_intervallo(testo)
                if ref_trovato:
                    for b_req in brani_list:
                        ref_req = analizza_intervallo(b_req)
                        if sono_sovrapposti(ref_req, ref_trovato):
                            item = {"t": testo.replace("•", "").strip(), "audio": None, "pdf": None}
                            # Il link sul testo è solitamente l'audio (.mp3)
                            h = urllib.parse.urljoin(url, a['href'])
                            if h.lower().endswith('.mp3'): item["audio"] = h
                            
                            # Cerca l'icona del PDF nei link immediatamente successivi
                            for j in range(i+1, i+5):
                                if j < len(links):
                                    h_next = urllib.parse.urljoin(url, links[j]['href'])
                                    if h_next.lower().endswith('.pdf') or 'trascrizioni' in h_next.lower():
                                        item["pdf"] = h_next
                                        break
                            if item["audio"] or item["pdf"]:
                                validi.append(item)
                            break
    except: pass
    # Rimuove duplicati
    visti, finale = set(), []
    for x in validi:
        if x['t'] not in visti:
            finale.append(x); visti.add(x['t'])
    return finale

def cerca_barzillai_chirurgico(brani_list, session, pagine):
    """Scansiona l'archivio Barzillai per trovare commenti di Don Romeo Cavedo."""
    validi = []
    for p in range(1, pagine + 1):
        url = f"http://www.barzillai.it/index.php?option=com_content&view=category&id=35&Itemid=158&limitstart={(p-1)*5}"
        try:
            res = session.get(url, timeout=10)
            soup = BeautifulSoup(res.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                testo = a.get_text().strip()
                ref_trovato = analizza_intervallo(testo)
                if ref_trovato:
                    for b_req in brani_list:
                        ref_req = analizza_intervallo(b_req)
                        if sono_sovrapposti(ref_req, ref_trovato):
                            validi.append({"t": testo, "u": urllib.parse.urljoin(url, a['href'])})
                            break
        except: break
    return validi

def verifica_qumran(url, session):
    """Verifica se un link di Qumran2 contiene effettivamente un commento."""
    try:
        return "Nessun commento trovato" not in session.get(url, timeout=5).text
    except:
        return False

def ricerca_collettiva_volto(brani, autori, session):
    """Cerca i commenti sul sito IlVolto.it per un set di autori."""
    mappa = {}
    for a_nome, a_id in autori.items():
        for b in brani:
            url = f"https://www.ilvolto.it/commenti_vangelo.php?autore={a_id}&vangelo={quote(b)}"
            try:
                soup = BeautifulSoup(session.get(url, timeout=5).text, 'html.parser')
                links = [l for l in soup.find_all('a', href=True) if 'visualizza_commento.php' in l['href']]
                if links:
                    if a_nome not in mappa: mappa[a_nome] = []
                    mappa[a_nome].append({"t": links[0].get_text().strip(), "u": urllib.parse.urljoin(url, links[0]['href']), "b": b})
            except: pass
    return mappa

def normalizza_liturgia(testo):
    """Pulisce le stringhe delle feste liturgiche per facilitare il confronto."""
    t = testo.upper().replace("ª", "A").replace("º", "O").replace("°", "A")
    return re.sub(r'\s+', ' ', t).strip()

# --- 5. CARICAMENTO E GESTIONE DATABASE WORD ---
url_db = "https://www.dropbox.com/scl/fi/v6t6gaj7m0vuxu7v6v69y/corrispondenze_liturgiche.docx?rlkey=f7v7v7v7v7v7v7v7v&dl=1"
nome_file = "database_liturgico.docx"

if not os.path.exists(nome_file):
    with open(nome_file, 'wb') as f: f.write(requests.get(url_db).content)

try:
    doc = Document(nome_file)
    db = []
    for row in doc.tables[0].rows[1:]:
        if len(row.cells) >= 2:
            db.append({"festa": row.cells[0].text.strip(), "vangelo": row.cells[1].text.strip()})
except:
    st.error("Errore nel caricamento del Database Word.")
    db = []

# --- 6. INTERFACCIA UTENTE (SIDEBAR) ---
AUTORI_QUMRAN = {"Paolo Curtaz": 366, "Enzo Bianchi": 3, "Luigi Maria Epicoco": 1097}
AUTORI_VOLTO = {"Ermes Ronchi": 1, "Antonio Savone": 4}

with st.sidebar:
    st.title("📖 Assistente Vangelo")
    txt_input = st.text_input("Inserisci Festa (es. 30a TO B) o Brano:", key="input_ricerca")
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1: btn_cerca = st.button("🔍 Cerca")
    with col_btn2: btn_oggi = st.button("📅 Oggi")
    
    st.divider()
    st.write("📊 **Gestione Database**")
    if st.button("🔄 Aggiorna Database", use_container_width=True):
        with st.spinner("Scaricando nuova versione da Dropbox..."):
            r = requests.get(url_db, allow_redirects=True)
            with open(nome_file, 'wb') as f: f.write(r.content)
            st.success("Database aggiornato con successo!"); st.rerun()
    
    # Tasto per consultare l'anteprima del Word
    url_anteprima = url_db.replace("&dl=1", "&dl=0")
    st.link_button("📂 Consulta Database", url_anteprima, use_container_width=True)

# --- 7. LOGICA DI RICERCA ---
if btn_cerca or btn_oggi or st.session_state.get("vai_alla_ricerca"):
    st.session_state["vai_alla_ricerca"] = False
    if not st.session_state.get("testo_ricerca") or btn_cerca or btn_oggi:
        st.session_state["testo_ricerca"] = txt_input
    
    brano_id = ""
    testo_pulito = st.session_state["testo_ricerca"]

    if btn_oggi:
        st.session_state["is_oggi"] = True
        try:
            res = session.get("https://www.apostolesacrocuore.org/vangelo-oggi-ambrosiano.php", timeout=10)
            tag = BeautifulSoup(res.text, 'html.parser').find(['h3', 'b', 'strong'], text=re.compile(r'(Mt|Mc|Lc|Gv)\s+\d+'))
            if tag: brano_id = re.search(r'(Mt|Mc|Lc|Gv)\s+\d+.*', tag.text, re.IGNORECASE).group(0)
        except: pass
    elif testo_pulito:
        st.session_state["is_oggi"] = False
        # Se l'utente scrive direttamente il vangelo
        if any(testo_pulito.upper().startswith(p) for p in ["MT", "MC", "LC", "GV"]):
            brano_id = testo_pulito
        else:
            # Ricerca per festa liturgica nel database Word
            in_norm = normalizza_liturgia(testo_pulito)
            feste = [i for i in db if all(re.search(rf'\b{re.escape(p)}\b', normalizza_liturgia(i['festa'])) for p in in_norm.split())]
            match_esatto = [i for i in feste if normalizza_liturgia(i['festa']) == in_norm]
            if match_esatto: feste = match_esatto

            if len({f['vangelo'] for f in feste}) > 1:
                st.warning("⚠️ Ambiguità trovata. Specifica meglio:")
                for f in feste:
                    st.button(f['festa'], key=f"btn_{f['festa']}", 
                              on_click=lambda n=f['festa']: st.session_state.update({"testo_ricerca": n, "vai_alla_ricerca": True}))
                st.stop()
            elif feste: 
                brano_id = feste[0]['vangelo']
            else:
                # Se non trovato nel Word, chiediamo a Gemini di indovinare il tema
                try:
                    resp = client.models.generate_content(model=NOME_MODELLO, contents=f"Trova il brano evangelico per il tema: '{testo_pulito}'. Rispondi solo con la citazione (es. Gv 4,5-42) o 'NULLA'.").text.strip()
                    if any(p in resp.upper() for p in ["MT", "MC", "LC", "GV"]): brano_id = resp
                    else: st.error("Nessun brano trovato per questa festa o tema."); st.stop()
                except: st.error("Errore di connessione."); st.stop()

    if brano_id:
        st.subheader(f"📖 Risultati per: {brano_id}")
        brani_c = espandi_matrioska(brano_id)
        
        t1, t2, t3, t4 = st.tabs(["✍️ Testo", "👤 Autori", "🏛️ Barzillai", "🏡 Villapizzone"])
        
        with t1:
            st.markdown("### Testo del Vangelo")
            try:
                res = client.models.generate_content(model=NOME_MODELLO, contents=f"Trascrivi il testo sacro di {brano_id}. Vai a capo dopo ogni versetto.")
                st.markdown(f"```\n{res.text.replace('**','').strip()}\n```")
            except: st.warning("Il servizio di trascrizione è temporaneamente occupato. Riprova.")

        with t2:
            # Video Chiesa di Milano (solo se cercato per "Oggi")
            if st.session_state.get("is_oggi"):
                url_p = "https://www.youtube.com/playlist?list=PLv-N1jjgsWgqThUFZ4oAooM8nbd25QMgj"
                st.markdown(f"📺 **[Guarda il Commento Video di oggi (Chiesa di Milano)]({url_p})**")
                st.caption("Il link apre la lista: clicca sul primo video (il più recente).")
                st.write("---")

            # Ricerca Autori Classici
            mappa_v = ricerca_collettiva_volto(brani_c, AUTORI_VOLTO, session)
            trovato = False
            for autore in sorted(list(set(list(AUTORI_QUMRAN.keys()) + list(AUTORI_VOLTO.keys())))):
                res_q = []
                if autore in AUTORI_QUMRAN:
                    for b in brani_c:
                        u = f"https://www.qumran2.net/parolenuove/commenti.php?criteri=1&autore={AUTORI_QUMRAN[autore]}&parole={quote(b)}"
                        if verifica_qumran(u, session): res_q.append({"b": b, "u": u})
                res_v = mappa_v.get(autore, [])
                if res_q or res_v:
                    trovato = True
                    with st.expander(f"👤 {autore}", expanded=True):
                        for r in res_q: st.write(f"✅ Qumran ({r['b']}): [Link]({r['u']})")
                        for r in res_v: st.write(f"✅ IlVolto ({r['b']}): [{r['t']}]({r['u']})")
            
            if not trovato: st.info("Nessun commento trovato per questi autori.")
            
            # Sezione "Nella Parola"
            st.divider()
            st.write("📖 **Nella Parola (Semeraro & Pasolini)**")
            for b in brani_c:
                b_f = re.sub(r'^([A-Z][a-z]?)(\d)', r'\1 \2', b.replace(" ", ""))
                st.markdown(f"👉 **[Commenti su {b_f}](https://nellaparola.it/commenti#s={quote(b_f)})**")

        with t3:
            st.markdown("### Don Romeo Cavedo (104 pagine)")
            lb = cerca_barzillai_chirurgico(brani_c, session, 104)
            if lb:
                for x in lb: st.write(f"✅ [{x['t']}]({x['u']})")
            else: st.warning("Nessun commento trovato in Barzillai.")

        with t4:
            st.markdown("### Gesuiti Villapizzone (Audio & PDF)")
            lv = cerca_villapizzone(brani_c, session)
            if lv:
                for v in lv:
                    lnks = []
                    if v['audio']: lnks.append(f"[🔊 Audio]({v['audio']})")
                    if v['pdf']: lnks.append(f"[📄 PDF]({v['pdf']})")
                    st.write(f"✅ {v['t']}: {' | '.join(lnks)}")
            else: st.warning("Nessun commento trovato su Villapizzone per questo brano.")
