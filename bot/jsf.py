from bs4 import BeautifulSoup
import re


def extract_viewstate(html, fallback=None):
    if "javax.faces.ViewState" in html:
        match = re.search(
            r'id=".*?javax\.faces\.ViewState.*?"><!\[CDATA\[(.*?)]]>',
            html
        )
        return match.group(1) if match else fallback
    else: return fallback

def form_verilerini_topla(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    form = soup.find("form", id="mainForm")
    if not form: return {}
    payload = {}
    for tag in form.find_all(["input", "select"]):
        name = tag.get("name")
        if not name: continue
        if tag.name == "input":
            value = tag.get("value", "")
            if tag.get("type") in ["checkbox", "radio"]:
                if tag.has_attr("checked"): payload[name] = value
            else: payload[name] = value
        elif tag.name == "select":
            selected = tag.find("option", selected=True)
            payload[name] = selected.get("value", "") if selected else ""
    return payload

def jsf_ajax_payload(source, execute="@all", render=None, viewstate=None):
    payload = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": source,
        "javax.faces.partial.execute": execute,
        source: source,
        "mainForm": "mainForm"
    }
    if render:
        payload["javax.faces.partial.render"] = render
    if viewstate:
        payload["javax.faces.ViewState"] = viewstate
    return payload

def auto_resolve_jsf_states(session, initial_res, current_url, max_depth=5):
    current_res = initial_res
    depth = 0
    positive_indicators = re.compile(r'(confirm|yes|ok|continue|onayla|accept)', re.IGNORECASE)
    
    while depth < max_depth:
        # 1. ViewState'i çek
        vs_match = re.search(r'id=".*?javax\.faces\.ViewState.*?"><!\[CDATA\[(.*?)]]>', current_res.text)
        current_vs = vs_match.group(1) if vs_match else None
        
        # 2. DIŞ KATMAN (XML PARSER)
        # Hata buradaydı: JSF partial-response verisi bir XML'dir. CDATA'nın kaybolmaması için 'xml' motoru şarttır.
        soup = BeautifulSoup(current_res.text, 'xml')
        update_tags = soup.find_all("update")
        
        # XML'in içindeki CDATA (HTML bloğu) metne çevrilir
        html_to_check = "".join([tag.text for tag in update_tags]) if update_tags else current_res.text
        
        # 3. İÇ KATMAN (HTML PARSER)
        # Çıkarılan bu ham metin artık saf HTML'dir.
        inner_soup = BeautifulSoup(html_to_check, 'html.parser')

        target_btn_id = None
        
        # BS4 içindeki class niteliği bir liste döner. Liste kontrolü yapılıyor.
        yes_btn = inner_soup.find(["button", "a"], class_=lambda c: c and "ui-confirmdialog-yes" in c)
        
        if not yes_btn:
            buttons = inner_soup.find_all(["button", "a"])
            for btn in buttons:
                btn_text = btn.get_text(strip=True)
                if positive_indicators.search(btn_text) or positive_indicators.search(btn.get("id", "")):
                    yes_btn = btn
                    break

        if yes_btn and yes_btn.get("id"):
            target_btn_id = yes_btn.get("id")
        else:
            # Hedef buton yoksa işlem pürüzsüzdür, döngüyü kır.
            break
            
        print(f"[*] Otonom Çözücü: Beklenmeyen bir onay adımı tespit edildi. Geçiliyor... (Buton: {target_btn_id})")
        
        base_form_data = form_verilerini_topla(html_to_check)
        payload = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": target_btn_id,
            "javax.faces.partial.execute": target_btn_id,
            target_btn_id: target_btn_id,
            "formLogo":"formLogo"
        }
        if current_vs:
            payload["javax.faces.ViewState"] = current_vs
            
        final_payload = {**base_form_data, **payload}

        # İsteği fırlat ve sunucunun yeni durumunu (state) current_res üzerine yaz
        current_res = session.post(current_url, data=final_payload, headers={"Referer": current_url}, timeout=45)
        depth += 1

    if depth >= max_depth:
        print("[!] Otonom Çözücü Hata: Maksimum derinliğe ulaşıldı, sonsuz döngü riski.")
        
    return current_res, depth
