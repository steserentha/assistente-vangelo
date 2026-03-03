import streamlit as st
import google.genai as genai
from docx import Document
import requests
from bs4 import BeautifulSoup
import re
import urllib.parse
import os

# --- 1. CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Assistente Liturgico", page_icon="📖", layout="wide")

# --- 2. RECUPERO API KEY DAI SECRETS ---
try:
    api_key = st.secrets["GEMINI_API_KEY"]
    client = genai.Client(api_key=api_key)
    NOME_MODELLO = "gemini-2.5-flash"
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
except Exception as e:
    st.error("Configurazione API Key mancante nei Secrets di Streamlit.")
    st.stop()

# --- 3. FUNZIONI LOGICHE (Validate 3.x - 5.x) ---
def normalizza_liturgia(testo):
    t = testo.lower().strip()
    mappa = {r'\bprima\b|\bi\b|\b1\b|\b1a\b': '1a', r'\bseconda\b|\bii\b|\b2\b|\b2a\b': '2a', r'\bterza\b|\biii\b|\b3\b|\b3a\b': '3a', r'\bquarta\b|\biv\b|\b4\b|\b4a\b': '4a', r'\bquinta\b|\bv\b|\b5\b|\b5a\b': '5a', r'\bsesta\b|\bvi\b|\b6\b|\b6a\b': '6a', r'\bavv\b': 'avvento', r'\bpas\b': 'pasqua', r'\bqua\b': 'quaresima', r'\bord\b|\bto\b': 'to', r'\bpen\b': 'pentecoste', r'\bepi\b': 'epifania', r'\bamb\b': 'amb', r'\brom\b': 'rom'}
    for pattern, sostituto in mappa.items(): t = re.sub(pattern, sostituto, t)
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

# --- 4. INTERFACCIA UTENTE ---
AUTORI_QUMRAN = {"Fabio Rosini": 944, "Luigi Epicoco": 948, "Cristiano Mauri": 919, "Angelo Casati": 941, "Paolo Curtaz": 827}
AUTORI_VOLTO = {"Fabio Rosini": ["fabio rosini", "don fabio rosini"], "Luigi Epicoco": ["luigi maria epicoco", "don luigi maria epicoco"], "Enzo Bianchi": ["enzo bianchi"], "Cristiano Mauri": ["cristiano mauri"], "Paolo Curtaz": ["paolo curtaz"]}

st.title("📖 Assistente Liturgico")
query = st.text_input("Brano, festa o tema:", placeholder="Es: samaritana, 3a ord rom")

col1, col2 = st.columns([1, 4])
btn_cerca = col1.button("🔍 Cerca", type="primary")
btn_oggi = col2.button("📅 Oggi")

if btn_cerca or btn_oggi:
    with st.spinner("Analisi in corso..."):
        nome_file = 'Liturgia_semplificata.docx'
        url_db = "https://www.dropbox.com/scl/fi/5gy6cpa4ve481m09519tb/Liturgia-semplificata.docx?rlkey=hs0wsu76p04nxuj9mwtim5yv2&dl=1"
        if not os.path.exists(nome_file):
            r = requests.get(url_db, allow_redirects=True)
            with open(nome_file, 'wb') as f: f.write(r.content)

        doc = Document(nome_file)
        db = [{"festa": p.text.split("|")[0].replace("[", "").replace("]", "").strip(), "vangelo": p.text.split("|")[1].strip(), "analisi": analizza_intervallo(p.text.split("|")[1].strip())} for p in doc.paragraphs if "|" in p.text]

        brano_id = ""
        if btn_oggi:
            try:
                res = session.get("https://www.apostolesacrocuore.org/vangelo-oggi-ambrosiano.php", timeout=10)
                tag = BeautifulSoup(res.text, 'html.parser').find(['h3', 'b', 'strong'], text=re.compile(r'(Mt|Mc|Lc|Gv)\s+\d+'))
                if tag: brano_id = re.search(r'(Mt|Mc|Lc|Gv)\s+\d+.*', tag.text, re.IGNORECASE).group(0)
            except: pass
        elif any(query.upper().startswith(p) for p in ["MT", "MC", "LC", "GV"]):
            brano_id = query
        else:
            in_norm = normalizza_liturgia(query)
            feste = [i for i in db if all(p in normalizza_liturgia(i['festa']) for p in in_norm.split())]
            if len({f['vangelo'] for f in feste}) > 1:
                st.warning("⚠️ Ambiguità: specifica l'anno.")
                for f in feste: st.write(f"- {f['festa']} ({f['vangelo']})")
                st.stop()
            elif feste: brano_id = feste[0]['vangelo']
            else:
                resp = client.models.generate_content(model=NOME_MODELLO, contents=f"Tema '{query}' -> brano (es. Gv 4,5-42) o 'NULLA'.").text.strip()
                if any(p in resp.upper() for p in ["MT", "MC", "LC", "GV"]): brano_id = resp
                else: st.error("Nessun risultato."); st.stop()

        if brano_id:
            st.subheader(f"📍 Vangelo: {brano_id}")
            an_req = analizza_intervallo(brano_id)
            ricorrenze = [i for i in db if sono_sovrapposti(an_req, i['analisi'])]
            
            # Pulizia duplicati (Logica 5.4)
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

            t1, t2, t3 = st.tabs(["✍️ Testo", "👤 Autori", "🏛️ Barzillai"])
            
            with t1:
                st.markdown("### Testo del Vangelo")
                p_bib = f"Trascrivi il brano {brano_id}. REGOLE: 1 versetto per riga. Riporta SOLO il testo: NO numeri, NO codici. No grassetti."
                try:
                    risposta = client.models.generate_content(model=NOME_MODELLO, contents=p_bib)
                    st.markdown(f"```\n{risposta.text.replace('**','').strip()}\n```")
                except Exception as e:
                    # Diagnostica Billing/Safety
                    st.error(f"Errore Tecnico Google: {str(e)}")
                    st.info("Se leggi 'Billing not enabled', controlla il metodo di pagamento su Google AI Studio.")

            with t2:
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

            with t3:
                st.markdown("### Don Romeo Cavedo (60 pagine)")
                lb = cerca_barzillai_chirurgico(brani_c, session, 60)
                if lb:
                    for x in lb: st.write(f"✅ [{x['t']}]({x['u']})")
                else: st.warning("Nulla in Barzillai.")
