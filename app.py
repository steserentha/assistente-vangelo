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
st.set_page_config(page_title="Assistente Liturgico", page_icon="📖", layout="wide")

st.markdown("""
<style>
/* Migliora la leggibilità del testo sacro e dei commenti */
.stMarkdown p, .stMarkdown li, .stMarkdown span, code, pre {
    white-space: pre-wrap !important;
    word-break: break-word !important;
    overflow-wrap: break-word !important;
    font-size: 1.1rem !important;
    font-family: 'Inconsolata', 'Tahoma', 'Times New Roman', serif !important;
}
/* Nasconde i testi tecnici delle icone nella barra laterale */
[data-testid="stSidebarNav"] span { white-space: nowrap !important; }
/* Pulsanti della sidebar a tutta larghezza per facilità d'uso */
div.stButton > button { 
    width: 100% !important; 
    margin-bottom: 5px; 
}
</style>
""", unsafe_allow_html=True)

# --- 2. RECUPERO API KEY E CONFIGURAZIONE MODELLO ---
try:
    api_key = st.secrets["GEMINI_API_KEY"]
    client = genai.Client(api_key=api_key)
    # Utilizziamo Gemini 2.5 Flash come richiesto
    NOME_MODELLO = "gemini-2.5-flash" 
    session = requests.Session()
    # User-Agent fondamentale per bypassare i blocchi di Villapizzone e Barzillai
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
    })
except Exception as e:
    st.error("Errore: Configurazione API Key mancante nei Secrets di Streamlit.")
    st.stop()

# --- 3. LOGICA BIBLICA E GESTIONE MATRIOSKE ---
def analizza_intervallo(testo):
    """Estrae libro, capitolo e versetti (es: Gv 10, 7-21)."""
    m = re.search(r'(Mt|Mc|Lc|Gv)\s*(\d+)\s*,\s*(\d+)(?:\s*-\s*(\d+))?', testo, re.IGNORECASE)
    if m:
        lib, cap, ini, fin = m.groups()
        return {"l": lib.capitalize(), "c": int(cap), "s": int(ini), "e": int(fin if fin else ini)}
    return None

def sono_sovrapposti(r1, r2):
    """Verifica se due brani hanno versetti in comune (Logica Matrioska)."""
    if r1['l'] != r2['l'] or r1['c'] != r2['c']: return False
    return max(r1['s'], r2['s']) <= min(r1['e'], r2['e'])

def espandi_matrioska(brano):
    """Crea una lista di brani per cercare corrispondenze puntuali."""
    m = analizza_intervallo(brano)
    if not m: return [brano]
    res = [brano]
    for i in range(m['s'], m['e'] + 1):
        res.append(f"{m['l']} {m['c']},{i}")
    return res

# --- 4. FUNZIONI DI RICERCA COMMENTI ---

def cerca_villapizzone(brani_list, session):
    """Versione sbloccata che scansiona correttamente la pagina van.html."""
    validi = []
    url = "https://www.gesuiti-villapizzone.it/sito/van.html"
    try:
        res = session.get(url, timeout=15)
        res.encoding = 'utf-8'
        soup = BeautifulSoup(res.text, 'html.parser')
        # Recuperiamo tutti i link (<a>) della pagina
        links = soup.find_all('a')
        
        for i, a in enumerate(links):
            testo = a.get_text().strip()
            # Se il link contiene un riferimento a un Vangelo (Mt, Mc, Lc, Gv)
            if any(lib in testo for lib in ["Mt", "Mc", "Lc", "Gv"]):
                ref_trovato = analizza_intervallo(testo)
                if ref_trovato:
                    for b_req in brani_list:
                        ref_req = analizza_intervallo(b_req)
                        if sono_sovrapposti(ref_req, ref_trovato):
                            # Trovata corrispondenza!
                            item = {"t": testo.replace("•", "").strip(), "audio": None, "pdf": None}
                            # L'audio è solitamente il link del testo stesso
                            h_main = urllib.parse.urljoin(url, a['href'])
                            if h_main.lower().endswith('.mp3'):
                                item["audio"] = h_main
                            
                            # Il PDF è l'icona rossa SUBITO DOPO (nei link successivi)
                            for j in range(i + 1, min(i + 5, len(links))):
                                a_next = links[j]
                                h_next = urllib.parse.urljoin(url, a_next['href'])
                                if h_next.lower().endswith('.pdf') or 'trascrizioni' in h_next.lower():
                                    item["pdf"] = h_next
                                    break
                            
                            if item["audio"] or item["pdf"]:
                                validi.append(item)
                            break
    except: pass
    # Rimuoviamo i duplicati mantenendo l'ordine
    visti, finale = set(), []
    for x in validi:
        if x['t'] not in visti:
            finale.append(x)
            visti.add(x['t'])
    return finale

def cerca_barzillai_chirurgico(brani_list, session, pagine):
    """Scansiona l'archivio di Don Romeo Cavedo (Barzillai)."""
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
    """Verifica l'effettiva presenza di un commento su Qumran2."""
    try:
        return "Nessun commento trovato" not in session.get(url, timeout=5).text
    except:
        return False

def ricerca_collettiva_volto(brani, autori, session):
    """Cerca i commenti sul portale IlVolto.it."""
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
    """Pulisce le stringhe per facilitare il confronto con il database Word."""
    t = testo.upper().replace("ª", "A").replace("º", "O").replace("°", "A")
    return re.sub(r'\s+', ' ', t).strip()

# --- 5. CARICAMENTO E GESTIONE DATABASE WORD (DROPBOX) ---
# Link al file Word dell'utente
url_db = "https://www.dropbox.com/scl/fi/5gy6cpa4ve481m09519tb/Liturgia-semplificata.docx?rlkey=hs0wsu76p04nxuj9mwtim5yv2&st=4rlqcpnp&dl=1"
nome_file = "database_liturgico.docx"

def scarica_db():
    try:
        r = requests.get(url_db, allow_redirects=True, timeout=15)
        if r.status_code == 200:
            with open(nome_file, 'wb') as f: f.write(r.content)
            return True
    except: pass
    return False

def carica_db():
    """Legge la tabella delle corrispondenze dal file Word."""
    if not os.path.exists(nome_file): 
        scarica_db()
    try:
        doc = Document(nome_file)
        data = []
        # Legge la prima tabella del documento
        for row in doc.tables[0].rows[1:]:
            if len(row.cells) >= 2:
                data.append({"festa": row.cells[0].text.strip(), "vangelo": row.cells[1].text.strip()})
        return data
    except Exception as e:
        if os.path.exists(nome_file): 
            try: os.remove(nome_file)
            except: pass
        return []

db = carica_db()

# --- 6. INTERFACCIA UTENTE (BARRA LATERALE) ---
st.title("📖 Assistente Vangelo")

with st.sidebar:
    st.header("🔍 Ricerca")
    txt_input = st.text_input("Festa (es. 30a TO B) o Brano:", key="input_ricerca", placeholder="Cerca qui...")
    
    col_b1, col_b2 = st.columns(2)
    with col_b1: btn_cerca = st.button("🔍 Cerca")
    with col_b2: btn_oggi = st.button("📅 Oggi")
    
    st.divider()
    st.write("📊 **Gestione Database**")
    if st.button("🔄 Aggiorna Database", use_container_width=True):
        with st.spinner("Scaricando..."):
            if scarica_db():
                st.success("Database aggiornato!"); st.rerun()
            else: 
                st.error("Errore nel download dal link Dropbox.")
    
    # Consultazione online con dl=0
    url_anteprima = url_db.replace("&dl=1", "&dl=0")
    st.link_button("📂 Consulta Database", url_anteprima, use_container_width=True)

# Errore se il database non viene caricato correttamente
if not db:
    st.error("⚠️ Il database è vuoto o non leggibile. Controlla il link Dropbox o clicca su 'Aggiorna Database'.")

# --- 7. LOGICA DI ESECUZIONE RICERCA ---
AUTORI_QUMRAN = {"Paolo Curtaz": 366, "Enzo Bianchi": 3, "Luigi Maria Epicoco": 1097}
AUTORI_VOLTO = {"Ermes Ronchi": 1, "Antonio Savone": 4}

# Gestione della ricerca (manuale, automatica o da pulsante "Oggi")
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
        # Caso 1: Ricerca diretta per brano (es: Gv 1,1)
        if any(testo_pulito.upper().startswith(p) for p in ["MT", "MC", "LC", "GV"]):
            brano_id = testo_pulito
        else:
            # Caso 2: Ricerca per festa nel database Word
            in_norm = normalizza_liturgia(testo_pulito)
            feste = [i for i in db if all(re.search(rf'\b{re.escape(p)}\b', normalizza_liturgia(i['festa'])) for p in in_norm.split())]
            match_esatto = [i for i in feste if normalizza_liturgia(i['festa']) == in_norm]
            if match_esatto: feste = match_esatto

            if len({f['vangelo'] for f in feste}) > 1:
                st.warning("⚠️ Ambiguità trovata. Scegli la festa corretta:")
                for f in feste:
                    st.button(f['festa'], key=f"btn_{f['festa']}", 
                              on_click=lambda n=f['festa']: st.session_state.update({"testo_ricerca": n, "vai_alla_ricerca": True}))
                st.stop()
            elif feste: 
                brano_id = feste[0]['vangelo']
            else:
                # Caso 3: Interpelliamo l'AI se non troviamo nulla nel Word
                try:
                    resp = client.models.generate_content(model=NOME_MODELLO, contents=f"Trova il brano evangelico per la festa o il tema: '{testo_pulito}'. Rispondi solo con la citazione (es. Gv 4,5-42) o 'NULLA'.").text.strip()
                    if any(p in resp.upper() for p in ["MT", "MC", "LC", "GV"]): brano_id = resp
                    else: st.error("Nessun brano trovato per questo tema."); st.stop()
                except: st.error("Errore di comunicazione con Gemini."); st.stop()

    # --- 8. VISUALIZZAZIONE RISULTATI ---
    if brano_id:
        st.divider()
        st.subheader(f"📖 Risultati per: {brano_id}")
        brani_c = espandi_matrioska(brano_id)
        
        # Le quattro schede principali
        t1, t2, t3, t4 = st.tabs(["✍️ Testo", "👤 Autori", "🏛️ Barzillai", "🏡 Villapizzone"])
        
        with t1:
            st.markdown("### Testo del Vangelo")
            try:
                res = client.models.generate_content(model=NOME_MODELLO, contents=f"Trascrivi il testo sacro di {brano_id}. Regole: 1. Solo testo biblico. 2. Vai a capo dopo ogni versetto.")
                st.markdown(f"```\n{res.text.replace('**','').strip()}\n```")
            except: st.warning("Gemini è momentaneamente occupato. Riprova tra pochi istanti.")

        with t2:
            # --- LINK VIDEO COMMENTO (Dinamico per "Oggi") ---
            if st.session_state.get("is_oggi"):
                url_p = "https://www.youtube.com/playlist?list=PLv-N1jjgsWgqThUFZ4oAooM8nbd25QMgj"
                st.markdown(f"📺 **[Guarda il Commento Video di oggi (Chiesa di Milano)]({url_p})**")
                st.caption("Il link apre la lista: clicca sul primo video (il più recente).")
                st.write("---")

            # Ricerca commenti degli autori selezionati
            mappa_v = ricerca_collettiva_volto(brani_c, AUTORI_VOLTO, session)
            for autore in sorted(list(set(list(AUTORI_QUMRAN.keys()) + list(AUTORI_VOLTO.keys())))):
                res_q = []
                if autore in AUTORI_QUMRAN:
                    for b in brani_c:
                        u = f"https://www.qumran2.net/parolenuove/commenti.php?criteri=1&autore={AUTORI_QUMRAN[autore]}&parole={quote(b)}"
                        if verifica_qumran(u, session): res_q.append({"b": b, "u": u})
                res_v = mappa_v.get(autore, [])
                if res_q or res_v:
                    with st.expander(f"👤 {autore}", expanded=True):
                        for r in res_q: st.write(f"✅ Qumran ({r['b']}): [Link]({r['u']})")
                        for r in res_v: st.write(f"✅ IlVolto ({r['b']}): [{r['t']}]({r['u']})")
            
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
            else: st.warning("Nessun commento trovato nell'archivio Barzillai.")

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
