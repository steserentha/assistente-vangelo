import streamlit as st
import google.genai as genai
from docx import Document
import requests
from bs4 import BeautifulSoup
import re
import urllib.parse
from urllib.parse import quote
import os

# --- 1. CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Assistente Liturgico", page_icon="📖", layout="wide")

# --- 2. RECUPERO API KEY DAI SECRETS ---
try:
    api_key = st.secrets["GEMINI_API_KEY"]
    client = genai.Client(api_key=api_key)
    # Manteniamo il modello che preferisci
    NOME_MODELLO = "gemini-2.5-flash"
    session = requests.Session()
    # User-Agent fondamentale per bypassare i blocchi di Villapizzone e Barzillai
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
    })
except Exception as e:
    st.error("Configurazione API Key mancante nei Secrets di Streamlit.")
    st.stop()

# --- CSS PER ANDARE A CAPO SU SMARTPHONE ---
st.markdown("""
<style>
/* Uniforma dimensione, a capo e font per ogni elemento di testo */
.stMarkdown, .stText, code, pre, p, span, div {
    white-space: pre-wrap !important;
    word-break: break-word !important;
    overflow-wrap: break-word !important;
    font-size: 1.1rem !important;
    font-family: 'Inconsolata', 'Tahoma', 'Times New Roman', serif !important;
    font-weight: normal !important;
}

/* Rimuove i margini extra dei blocchi di codice che possono sfalsare la visualizzazione */
code, pre {
    padding: 0 !important;
    background-color: transparent !important;
}
</style>
""", unsafe_allow_html=True)

# --- 3. FUNZIONI LOGICHE ---
def normalizza_liturgia(testo):
    t = testo.lower().strip()
    mappa = {
        r'\bquar\b': 'QUA',
        r'\bprima\b|\bi\b|\b1\b|\b1a\b': '1a', 
        r'\bseconda\b|\bii\b|\b2\b|\b2a\b': '2a', 
        r'\bterza\b|\biii\b|\b3\b|\b3a\b': '3a', 
        r'\bquarta\b|\biv\b|\b4\b|\b4a\b': '4a', 
        r'\bquinta\b|\bv\b|\b5\b|\b5a\b': '5a', 
        r'\bsesta\b|\bvi\b|\b6\b|\b6a\b': '6a', 
        r'\bavv\b': 'avvento', 
        r'\bpas\b': 'pasqua', 
        r'\bqua\b': 'quaresima', 
        r'\bord\b|\bto\b': 'to', 
        r'\bpen\b': 'pentecoste', 
        r'\bepi\b': 'epifania', 
        r'\bamb\b': 'amb', 
        r'\brom\b': 'rom'
    }
    for pattern, sostituto in mappa.items(): 
        t = re.sub(pattern, sostituto, t)
    return t.upper()

def analizza_intervallo(riferimento):
    try:
        s = riferimento.replace(" ", "").replace("–", "-").replace("—", "-")
        m = re.search(r'(Mt|Mc|Lc|Gv)(\d+),(\d+)(?:-(?:(\d+),)?(\d+))?', s, re.IGNORECASE)
        if m:
            lib = m.group(1).capitalize()
            c1, v1 = int(m.group(2)), int(m.group(3))
            c2 = int(m.group(4)) if m.group(4) else c1
            v2 = int(m.group(5)) if m.group(5) else (v1 if not m.group(4) else 150)
            return (lib, (c1 * 1000) + v1, (c2 * 1000) + v2)
    except: pass
    return None

def sono_sovrapposti(r1, r2):
    if not r1 or not r2 or r1[0] != r2[0]: return False
    return r1[1] <= r2[2] and r2[1] <= r1[2]

def verifica_qumran(url, session):
    try:
        res = session.get(url, timeout=7)
        return not any(x in res.text for x in ["Nessun commento", "Nessun risultato", "0 documenti trovati"])
    except: return False

def verifica_tag_volto(url, brano, session):
    try:
        res = session.get(url, timeout=7)
        return brano.lower().replace(",", "") in res.text.lower().replace(",", "")
    except: return False

def ricerca_collettiva_volto(brani_list, autori_volto, session):
    risultati = {}
    for b in brani_list:
        tag = b.lower().replace(" ", "-").replace(",", "-").replace(":", "-").replace("–", "-")
        tag = re.sub(r'-+', '-', tag).strip("-")
        for p in range(1, 11): 
            url = f"https://www.cercoiltuovolto.it/tag/{tag}/" if p == 1 else f"https://www.cercoiltuovolto.it/tag/{tag}/page/{p}/"
            try:
                res = session.get(url, timeout=10)
                if res.status_code != 200: break
                soup = BeautifulSoup(res.text, 'html.parser')
                for a in soup.find_all('a', href=True):
                    u, txt = a['href'], a.get_text().strip()
                    if "/tag/" not in u and len(txt) > 15:
                        for autore, nomi in autori_volto.items():
                            if any(n in txt.lower() for n in nomi):
                                if verifica_tag_volto(u, b, session):
                                    if autore not in risultati: risultati[autore] = []
                                    risultati[autore].append({"t": txt, "u": u, "b": b})
            except: break
    return risultati

def pulisci_link_barzillai(tag_a):
    href, onclick = tag_a.get('href', ''), tag_a.get('onclick', '')
    match = re.search(r"'(.*?)'", href + onclick)
    if match:
        path = match.group(1)
        return f"http://www.barzillai.it/{path}" if not path.startswith('http') else path
    return href if href and not href.startswith('javascript') else None

def cerca_barzillai_chirurgico(brani_list, session, max_pagine=60): 
    validi, visti_url = [], set()
    for brano in brani_list:
        parti = re.split(r'(\d+|,|-|–)', brano.replace(" ", ""))
        regex_b = re.compile(r"\s*".join([re.escape(p) for p in parti if p]), re.IGNORECASE)
        for p in range(1, max_pagine + 1):
            url = "http://www.barzillai.it/index.php" if p == 1 else f"http://www.barzillai.it/index.php?pag={p}"
            try:
                res = session.get(url, timeout=10)
                res.encoding = 'latin-1'
                soup = BeautifulSoup(res.text, 'html.parser')
                blocchi = re.split(r'Data:', str(soup), flags=re.IGNORECASE)
                for blocco in blocchi:
                    if regex_b.search(blocco):
                        for a in BeautifulSoup(blocco, 'html.parser').find_all('a'):
                            t_l = a.get_text().upper()
                            if any(key in t_l for key in ["TESTO", "ASCOLTA", "AUDIO"]):
                                url_f = pulisci_link_barzillai(a)
                                if url_f and url_f not in visti_url:
                                    label = "📄 Testo" if "TESTO" in t_l else "🔊 Audio"
                                    validi.append({"t": f"{label} Barzillai ({brano})", "u": url_f})
                                    visti_url.add(url_f)
            except: break
    return validi

def cerca_villapizzone(brani_list, session):
    """Versione sbloccata che scansiona correttamente la pagina van.html."""
    validi = []
    url_van = "https://www.gesuiti-villapizzone.it/sito/van.html"
    try:
        # Headers per bypassare il blocco (come nel debug lab)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        res = session.get(url_van, headers=headers, timeout=15)
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
                            h_main = urllib.parse.urljoin(url_van, a['href'])
                            if h_main.lower().endswith('.mp3'):
                                item["audio"] = h_main
                            
                            # Il PDF è l'icona rossa SUBITO DOPO (nei link successivi)
                            for j in range(i + 1, min(i + 5, len(links))):
                                a_next = links[j]
                                h_next = urllib.parse.urljoin(url_van, a_next['href'])
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

# --- 4. INTERFACCIA UTENTE ---
AUTORI_QUMRAN = {"Fabio Rosini": 944, "Luigi Epicoco": 948, "Cristiano Mauri": 919, "Angelo Casati": 941, "Paolo Curtaz": 827}
AUTORI_VOLTO = {"Fabio Rosini": ["fabio rosini", "don fabio rosini"], "Luigi Epicoco": ["luigi maria epicoco", "don luigi maria epicoco"], "Enzo Bianchi": ["enzo bianchi"], "Cristiano Mauri": ["cristiano mauri"], "Paolo Curtaz": ["paolo curtaz"]}

# Definiamo subito i dati del database, così sono visibili anche alla sidebar
nome_file = 'Liturgia_semplificata.docx'
url_db = "https://www.dropbox.com/scl/fi/5gy6cpa4ve481m09519tb/Liturgia-semplificata.docx?rlkey=hs0wsu76p04nxuj9mwtim5yv2&st=4rlqcpnp&dl=1"

st.title("📖 Assistente Liturgico")

# Inizializziamo la memoria per gestire la barra di ricerca
if "testo_ricerca" not in st.session_state:
    st.session_state["testo_ricerca"] = ""

# La barra di ricerca
query = st.text_input("Brano, festa o tema:", key="input_query")

col1, col2 = st.columns([1, 4])
btn_cerca = col1.button("🔍 Cerca", type="primary")
btn_oggi = col2.button("📅 Oggi")

# Tasto manuale per aggiornare il file da Dropbox (nella sidebar)
with st.sidebar:
    st.divider()
    if st.button("🔄 Aggiorna Database"):
        with st.spinner("Scaricando nuova versione..."):
            r = requests.get(url_db, allow_redirects=True)
            with open(nome_file, 'wb') as f: f.write(r.content)
            st.success("Database aggiornato!")
            st.rerun()
    
    # --- NUOVO TASTO CONSULTA DATABASE ---
    # Creiamo il link di anteprima sostituendo dl=1 con dl=0
    url_anteprima = url_db.replace("dl=1", "dl=0")
    st.link_button("📂 Consulta Database", url_anteprima, use_container_width=True)

# La ricerca parte se premiamo Cerca, Oggi, o se un bottone ha impostato la ricerca automatica
if btn_cerca or btn_oggi or (query and not st.session_state.get("is_searching")) or st.session_state.get("vai_alla_ricerca"):
    if "vai_alla_ricerca" in st.session_state:
        del st.session_state["vai_alla_ricerca"]

    with st.spinner("Analisi in corso..."):
        # Se il file non esiste (primo avvio), lo scarichiamo
        if not os.path.exists(nome_file):
            r = requests.get(url_db, allow_redirects=True)
            with open(nome_file, 'wb') as f: f.write(r.content)

        # Carichiamo il database
        doc = Document(nome_file)
        db = [{"festa": p.text.split("|")[0].replace("[", "").replace("]", "").strip(), "vangelo": p.text.split("|")[1].strip(), "analisi": analizza_intervallo(p.text.split("|")[1].strip())} for p in doc.paragraphs if "|" in p.text]

        brano_id = ""
        # Sincronizziamo il testo ricerca con la query
        if query:
            st.session_state["testo_ricerca"] = query
        
        testo_pulito = st.session_state["testo_ricerca"]

        if btn_oggi:
            try:
                res = session.get("https://www.apostolesacrocuore.org/vangelo-oggi-ambrosiano.php", timeout=10)
                tag = BeautifulSoup(res.text, 'html.parser').find(['h3', 'b', 'strong'], text=re.compile(r'(Mt|Mc|Lc|Gv)\s+\d+'))
                if tag: brano_id = re.search(r'(Mt|Mc|Lc|Gv)\s+\d+.*', tag.text, re.IGNORECASE).group(0)
            except: pass
        elif testo_pulito and any(testo_pulito.upper().startswith(p) for p in ["MT", "MC", "LC", "GV"]):
            brano_id = testo_pulito
        elif testo_pulito:
            in_norm = normalizza_liturgia(testo_pulito)
            feste = [i for i in db if all(re.search(rf'\b{re.escape(p)}\b', normalizza_liturgia(i['festa'])) for p in in_norm.split())]
            
            match_esatto = [i for i in feste if normalizza_liturgia(i['festa']) == in_norm]
            if match_esatto:
                feste = match_esatto

            if len({f['vangelo'] for f in feste}) > 1:
                st.warning("⚠️ Ambiguità: specifica l'anno.")
                st.write("Seleziona quella corretta:")
                
                def clicca_opzione(nome):
                    st.session_state["testo_ricerca"] = nome
                    st.session_state["vai_alla_ricerca"] = True

                for f in feste:
                    nome_f = f['festa']
                    st.button(nome_f, key=f"btn_{nome_f}", on_click=clicca_opzione, args=(nome_f,))
                st.stop()
            elif feste: 
                brano_id = feste[0]['vangelo']
            else:
                resp = client.models.generate_content(model=NOME_MODELLO, contents=f"Tema '{testo_pulito}' -> brano (es. Gv 4,5-42) o 'NULLA'.").text.strip()
                if any(p in resp.upper() for p in ["MT", "MC", "LC", "GV"]): brano_id = resp
                else: st.error("Nessun risultato."); st.stop()

        if brano_id:
            st.subheader(f"📍 Vangelo: {brano_id}")
            an_req = analizza_intervallo(brano_id)
            ricorrenze = [i for i in db if sono_sovrapposti(an_req, i['analisi'])]

            if ricorrenze:
                nomi_feste = list(set([r['festa'] for r in ricorrenze]))
                st.info(f"🔗 **Corrispondenze:** {', '.join(nomi_feste)}")            
            
            brani_raw = [brano_id] + [r['vangelo'] for r in ricorrenze]
            brani_c, visti_norm = [], set()
            for b in brani_raw:
                b_norm = b.replace(" ", "").upper()
                if b_norm not in visti_norm:
                    brani_c.append(b)
                    visti_norm.add(b_norm)
            
            for b in list(brani_c):
                an = analizza_intervallo(b)
                if an and (an[2]//1000 > an[1]//1000): brani_c.append(f"{an[0]} {an[2]//1000}, 1-{an[2]%1000}")

            # --- DEFINIZIONE SCHEDE (TAB) ---
            t1, t2, t3, t4 = st.tabs(["✍️ Testo", "👤 Autori", "🏛️ Barzillai", "🏡 Villapizzone"])
            
            with t1:
                st.markdown("### Testo del Vangelo")
                p_bib = f"Trascrivi il testo sacro della Bibbia per la citazione: {brano_id}. REGOLE: 1. Usa SOLO il testo di {brano_id}. 2. Vai a capo dopo ogni versetto. 3. Nessun commento."
                try:
                    risposta = client.models.generate_content(model=NOME_MODELLO, contents=p_bib)
                    if risposta and hasattr(risposta, 'text') and risposta.text:
                        testo_finale = risposta.text.replace('**','').strip()
                        st.markdown(f"```\n{testo_finale}\n```")
                    else:
                        st.warning("⚠️ Gemini non ha risposto in tempo. Prova a cliccare di nuovo su Cerca.")
                except Exception as e:
                    st.error(f"Errore tecnico: {str(e)}")

            with t2:
                if st.session_state.get("is_oggi"):
                    url_playlist = "https://www.youtube.com/playlist?list=PLv-N1jjgsWgqThUFZ4oAooM8nbd25QMgj"
                    st.markdown(f"📺 **[Guarda il Commento Video di oggi (Chiesa di Milano)]({url_playlist})**")
                    st.caption("Il link apre la lista: clicca sul primo video (il più recente).")
                    st.write("---")

                mappa_volto = ricerca_collettiva_volto(brani_c, AUTORI_VOLTO, session)
                trovato_a = False
                for autore in sorted(list(set(list(AUTORI_QUMRAN.keys()) + list(AUTORI_VOLTO.keys())))):
                    res_q = []
                    if autore in AUTORI_QUMRAN:
                        for b in brani_c:
                            u_q = f"https://www.qumran2.net/parolenuove/commenti.php?criteri=1&autore={AUTORI_QUMRAN[autore]}&parole={urllib.parse.quote_plus(b.replace('–','-'))}"
                            if verifica_qumran(u_q, session): res_q.append({"b": b, "u": u_q})
                    res_v = mappa_volto.get(autore, [])
                    if res_q or res_v:
                        trovato_a = True
                        with st.expander(f"👤 {autore}", expanded=True):
                            for r in res_q: st.write(f"✅ Qumran ({r['b']}): [Link]({r['u']})")
                            for r in res_v: st.write(f"✅ IlVolto ({r['b']}): [{r['t']}]({r['u']})")
                
                if not trovato_a: st.info("Nessun commento trovato.")
                
                st.write("---")
                st.write("📖 **Nella Parola (Semeraro & Pasolini)**")
                for b in brani_c:
                    b_pulito = re.sub(r'(?<=\d)[a-z]', '', b, flags=re.IGNORECASE)
                    b_senza_spazi = b_pulito.replace(" ", "")
                    b_finale = re.sub(r'^([A-Z][a-z]?)(\d)', r'\1 \2', b_senza_spazi)
                    url_np = f"https://nellaparola.it/commenti#s={quote(b_finale)}"
                    st.markdown(f"👉 **[Commenti su {b_finale}]({url_np})**")

            with t3:
                st.markdown("### Don Romeo Cavedo (104 pagine)")
                lb = cerca_barzillai_chirurgico(brani_c, session, 104)
                if lb:
                    for x in lb: st.write(f"✅ [{x['t']}]({x['u']})")
                else: st.warning("Nulla in Barzillai.")

            with t4:
                st.markdown("### Gesuiti Villapizzone (Audio & PDF)")
                lv = cerca_villapizzone(brani_c, session)
                if lv:
                    for v in lv:
                        links_list = []
                        if v.get('audio'): links_list.append(f"[🔊 Audio]({v['audio']})")
                        if v.get('pdf'): links_list.append(f"[📄 PDF]({v['pdf']})")
                        st.write(f"✅ {v['t']}: {' | '.join(links_list)}")
                else: 
                    st.warning("Nessun commento trovato su Villapizzone.")
