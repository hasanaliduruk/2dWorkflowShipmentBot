from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime
import time
import re
import pandas as pd

from bot.constants import (
    BASE_URL,
    LOGIN_URL,
    DRAFT_PAGE_URL,
    PLAN_URL,
    USER_AGENT,
)
from bot.auth import login
from bot.jsf import form_verilerini_topla, extract_viewstate, jsf_ajax_payload
from bot.analysis import analizi_yap

def html_tabloyu_parse_et(mgr, html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    rows = soup.find_all("tr", role="row")
    if not rows: return pd.DataFrame()

    takip_edilen_tarihler = set(mgr.watch_list.keys())
    
    veri_listesi = []
    for row in rows:
        cells = row.find_all("td")
        if not cells or len(cells) < 11: continue
        try:
            name_input = cells[2].find("input")
            draft_name = name_input['value'] if name_input else cells[2].get_text(strip=True)
            name_input_id = name_input["id"]
            open_link = row.find("a", title="Open Draft Shipment")
            if not open_link: open_link = cells[1].find("a") 
            row_action_id = open_link.get("id") if open_link else None
            
            # Copy butonu bulma
            copy_link = row.find("a", title=lambda x: x and ("duplicate" in x.lower() or "copy" in x.lower()))
            if not copy_link:
                copy_icon = row.find("span", class_=lambda x: x and ("copy" in x or "clone" in x))
                if copy_icon: copy_link = copy_icon.find_parent("a")
            copy_action_id = copy_link.get("id") if copy_link else None

            from_loc = cells[3].get_text(strip=True)
            created_date = cells[10].get_text(strip=True)
            units = cells[9].get_text(strip=True)
            skus = cells[8].get_text(strip=True)
            
            # --- AUTO SELECT MANTIĞI ---
            # Eğer bu draft ismi, oluşturduğumuz kopyalar listesindeyse TRUE yap
            

            secili_mi = created_date in takip_edilen_tarihler
            veri_listesi.append({
                "Seç": secili_mi, # Dinamik seçim
                "Draft Name": draft_name,
                "From": from_loc,
                "SKUs": skus,
                "Units": units,
                "Created": created_date,
                "Action ID": row_action_id,
                "Copy ID": copy_action_id,
                "Name Input ID": name_input_id
            })
            
        except Exception as e: 
            print(e)
            continue
    return pd.DataFrame(veri_listesi)

def veriyi_dataframe_yap(mgr):
    if not mgr.session.cookies:
        if not login(mgr): return None, "Giriş Yapılamadı"
    try:
        response = mgr.session.get(DRAFT_PAGE_URL)
        if "login.jsf" in response.url: login(mgr); response = mgr.session.get(DRAFT_PAGE_URL, headers={"Referer": DRAFT_PAGE_URL})
        df = html_tabloyu_parse_et(mgr, response.text)
        
        if not df.empty:
            # --- NEW CONFIG COLUMNS ---
            # 1. Specific Mile Limit (Defaults to Global Setting)
            df["Max Mil"] = mgr.mile_threshold 
            # 2. Target Warehouses (Empty by default)
            df["Hedef Depolar"] = "" 
            
            return (df, None)
        else:
            return (None, "Tablo boş.")
    except Exception as e: return None, str(e)

def poll_results_until_complete(session, base_payload, referer_url):
    max_retries = 60
    last_percent = 0

    for i in range(max_retries):
        try:
            poll_params = jsf_ajax_payload("mainForm:planingStatusDialogPoll", render="mainForm:shipmentPlansPanel mainForm:a2dw_boxContentPanel mainForm:progressBarPlaning")
            res = session.post(PLAN_URL, data={**base_payload, **poll_params}, headers={"Referer": referer_url})
            
            vs = extract_viewstate(res.text, vs)
            if vs: base_payload["javax.faces.ViewState"] = vs

            #if "mainForm:plans" in res.text or "Amazon Optimized Splits" in res.text:
                #return res.text
            
            match_percent = re.search(r'>\s*(\d+)\s*%\s*<', res.text)
            current_percent = int(match_percent.group(1)) if match_percent else 0

            if current_percent == 0 and last_percent > 50: return res.text
            if current_percent > last_percent: last_percent = current_percent
            time.sleep(5)
        except: time.sleep(5)
    return None

def drafti_kopyala(mgr, target_date):
    """
    Kopyalama yapar ve YENİ OLUŞAN DRAFT'IN ADINI döndürür.
    """
    mgr.add_log("Kopyalama işlemi başlatılıyor...", "info")
    
    # 1. Target'dan draftı bul
    res = mgr.session.get(DRAFT_PAGE_URL)
    if "login.jsf" in res.url: login(mgr); res = mgr.session.get(DRAFT_PAGE_URL)
    
    df = html_tabloyu_parse_et(mgr, res.text)
    if df.empty: return None

    ilgili_satir = df[df["Created"] == target_date]
    if ilgili_satir.empty: 
        mgr.add_log("Kopyalanacak satır tarihle bulunamadı.", "error")
        return None
    
    copy_id = ilgili_satir.iloc[0]["Copy ID"]
    base_loc = str(ilgili_satir.iloc[0]["From"])
    if not copy_id: return None
        
    # 2. Copy Butonuna Bas
    form_data = form_verilerini_topla(res.text)
    copy_payload = jsf_ajax_payload(copy_id, "clone_draft_confirm")
    res_confirm = mgr.session.post(DRAFT_PAGE_URL, data={**form_data, **copy_payload})
    
    # 3. Confirm (Yes) Butonuna Bas
    confirm_btn_id = None
    try:
        match = re.search(r'button id="([^"]+)"[^>]*class="[^"]*ui-confirmdialog-yes', res_confirm.text)
        if match: confirm_btn_id = match.group(1)
    except: pass
    
    if not confirm_btn_id: return None
        
    current_vs = form_data.get("javax.faces.ViewState")
    try:
        match_vs = re.search(r'id=".*?javax\.faces\.ViewState.*?"><!\[CDATA\[(.*?)]]>', res_confirm.text)
        if match_vs: current_vs = match_vs.group(1)
    except: pass

    confirm_payload = jsf_ajax_payload(confirm_btn_id, viewstate=current_vs)
    res_final = mgr.session.post(DRAFT_PAGE_URL, data=confirm_payload)

    # 4. Redirect ve Yeni İsim Alma
    if "<redirect" in res_final.text:
        try:
            redirect_part = res_final.text.split('url="')[1].split('"')[0].replace("&amp;", "&")
            full_redirect_url = urllib.parse.urljoin(BASE_URL, redirect_part)
            
            # Yeni sayfaya git
            new_page_res = mgr.session.get(full_redirect_url)    
            soup_new = BeautifulSoup(new_page_res.text, 'html.parser')

            name_input = soup_new.find("input", {"name": lambda x: x and "draft_name" in x})
            new_draft_name = name_input.get("value") if name_input else "Bilinmeyen Kopya"

            loc_span = soup_new.find("span", {"id": "mainForm:draftInfo:0:ship_from_address"})
            new_location = loc_span.get_text(strip=True) if loc_span else ""

            mgr.add_log(f"✅ Kopyalandı: {new_draft_name}")
            
            if base_loc.lower() not in new_location.lower():
                mgr.add_log(f"📍 Adres düzeltiliyor: {new_location} -> {base_loc}", "warning")
                address_request_handler(mgr, full_redirect_url, target_date, new_page_res)
            
            time.sleep(2) # Sistemin oturması için
            res_check = mgr.session.get(DRAFT_PAGE_URL)
            soup_list = BeautifulSoup(res_check.text, 'html.parser')
            df_check = html_tabloyu_parse_et(mgr, res_check.text)
            yeni_satir = df_check[df_check["Draft Name"] == new_draft_name]

            if not yeni_satir.empty:
                yeni_tarih = yeni_satir.iloc[0]["Created"]
                loc = yeni_satir.iloc[0]["From"]
                new_input_id = yeni_satir.iloc[0]["Name Input ID"]
                clean_base = re.sub(r'(\s*-\s*copy|\s*copy|\s*-\s*clone)+', '', new_draft_name, flags=re.IGNORECASE).strip()
                # Eski tarihleri temizle
                clean_base = re.sub(r'\s\d{2}[/.-]\d{2}\s\d{2}:\d{2}:\d{2}$', '', clean_base)
                
                # Yeni Tarih Ekle (Gün/Ay Saat:Dk:Sn)
                unique_ts = datetime.now().strftime("%d/%m %H:%M:%S")
                if len(clean_base) > 30: clean_base = clean_base[:30]
                new_clean_name = f"{clean_base} {unique_ts}"
                
                # ViewState'i formdan al
                vs_input = soup_list.find("input", {"name": "javax.faces.ViewState"})
                current_vs = vs_input.get("value")
                
                # --- RENAME SEQUENCE ÇAĞIR ---
                if rename_draft_sequence(mgr, new_input_id, new_clean_name, soup_list, current_vs):
                    final_draft_name = new_clean_name
                    mgr.add_log(f"✏️ İsim düzeltildi: {new_clean_name}")
                else:
                    final_draft_name = new_draft_name
                
                # SUCCESS NOTIFICATION
                # teams_bildirim_gonder(
                #     mgr=mgr,
                #     title="Kopyalama Başarılı",
                #     message="Yeni taslak oluşturuldu ve takip listesine eklendi.",
                #     status="info",
                #     facts={
                #         "Eski Taslak": str(target_date), # Or original name if you pass it
                #         "Yeni Taslak": new_draft_name,
                #         "Lokasyon": loc,
                #         "Tarih": yeni_tarih
                #     }
                # )
                time.sleep(2) # Sistemin oturması için
                res_final_check = mgr.session.get(DRAFT_PAGE_URL)
                df_check = html_tabloyu_parse_et(mgr, res_final_check.text)
                yeni_satir = df_check[df_check["Draft Name"] == final_draft_name]

                if not yeni_satir.empty:
                    yeni_tarih = yeni_satir.iloc[0]["Created"]
                    loc = yeni_satir.iloc[0]["From"]
                return {"name": final_draft_name, "date": yeni_tarih, "loc": loc}
            else:
                mgr.add_log("⚠️ Kopyalanan satır listede bulunamadı (Rename atlandı).", "warning")
            return None
            
        except Exception as e: 
            print(f"Kopya isim hatası: {e}")
            return None
            
    return None

def drafti_planla_backend(mgr, draft_item):

    target_date = draft_item['date']
    draft_name = draft_item['name']
    try:
        # 1. Draft Aç
        mgr.add_log(f"İşlem başladı: {draft_name}", "info")
        main_res = mgr.session.get(DRAFT_PAGE_URL)
        if "login.jsf" in main_res.url: login(mgr); main_res = mgr.session.get(DRAFT_PAGE_URL)

        df = html_tabloyu_parse_et(mgr, main_res.text)
        target_row = df[df["Created"] == target_date]

        if target_row.empty:
            mgr.add_log(f"⚠️ {draft_name} listede bulunamadı! (Tarih eşleşmedi)", "warning")
            return None
        current_action_id = target_row.iloc[0]["Action ID"]

        form_data = form_verilerini_topla(main_res.text)
        action_payload = jsf_ajax_payload(current_action_id)
        res_open = mgr.session.post(DRAFT_PAGE_URL, data={**form_data, **action_payload})
        
        # Redirect Check
        redirect_url = None
        if "<redirect" in res_open.text:
            try:
                redirect_part = res_open.text.split('url="')[1].split('"')[0].replace("&amp;", "&")
                redirect_url = urllib.parse.urljoin(BASE_URL, redirect_part)
            except: pass
        
        if not redirect_url:
            mgr.add_log(f"{draft_name} açılamadı.", "error")
            return None # Return None = Kopyalama olmadı

        mgr.session.get(redirect_url) # Detay sayfası
        
        # 2. Planlama
        mgr.add_log("🚀 Planlama başlatılıyor...")
        detay_res = mgr.session.get(redirect_url)
        detay_form_data = form_verilerini_topla(detay_res.text)
        create_plan_params = jsf_ajax_payload("mainForm:create_plan", render="mainForm")
        res_plan = mgr.session.post(PLAN_URL, data={**detay_form_data, **create_plan_params}, headers={"Referer": redirect_url})
        
        if "ui-messages-error" in res_plan.text:
             mgr.add_log("Planlama hatası.", "error")
             return None

        # 3. Polling
        vs = extract_viewstate(res_plan.text, vs)
        if vs: detay_form_data["javax.faces.ViewState"] = vs

        final_xml = final_xml = poll_results_until_complete(
            mgr.session, 
            detay_form_data, 
            redirect_url, 
        )
        
        if final_xml:
            sonuc = analizi_yap(mgr, final_xml, draft_item)
            if isinstance(sonuc, dict) and 'found_target' in sonuc:
                mgr.add_log(f"🏁 {draft_name}: Hedef depo bulunduğu için işlem sonlandırıldı.", "success")
                return {"STOP": sonuc['found_target']} # This removes it from the watchlist
            
            elif isinstance(sonuc, dict) and 'found_new' in sonuc:
                found_wh = sonuc['found_new']
                
                # Copy using date
                yeni_draft_verisi = drafti_kopyala(mgr, target_date)
                
                if yeni_draft_verisi:
                    # Return the new warehouse so it can be saved to the new item
                    yeni_draft_verisi['newly_found_warehouse'] = found_wh
                    
                    mgr.add_log(f"🔄 {draft_name} kopyalandı ({found_wh}).", "success")
                    return yeni_draft_verisi
            
            mgr.add_log(f"{draft_name} tamamlandı, fırsat yok.", "warning")
            return None
            
        return None

    except Exception as e:
        mgr.add_log(f"Hata ({draft_name}): {str(e)}", "error")
        return None

def address_request_handler(mgr, draft_url, target_date, res_draft):

    # Get location:
    draft_data = mgr.watch_list.get(target_date)
    
    if not draft_data:
        print(f"❌ Error: {target_date} not found in watchlist.")
        return None
        
    location_value = draft_data["loc"]
    print(f"📍 Target Location: {location_value}")
    
    # Request the draft page:

    # res_draft = manager.session.get(draft_url)
    form_data = form_verilerini_topla(res_draft.text)
    current_viewstate = form_data.get("javax.faces.ViewState")
    draft_soup = BeautifulSoup(res_draft.text, "html.parser")

    # find the id of secret button
    # STRICT SEARCH: Find the script tag containing the specific function name
    # We use re.compile to match the content partially
    secret_btn_id = ""
    target_script = draft_soup.find('script', string=re.compile(r'updateAddress\s*='))

    if target_script and target_script.has_attr('id'):
        found_id = target_script['id']
        print(f"Found ID: {found_id}")
        secret_btn_id = found_id
    else:
        print("Target script not found or has no ID.")
    # Find pencil:

    edit_link = draft_soup.find("a", title="Change 'Ship From' address")
    if not edit_link: edit_link = draft_soup.find("a", id=re.compile(r"ship_from_address_edit"))
    if not edit_link:
        pencil_icon = draft_soup.find("i", class_="pi-pencil")
        if pencil_icon: edit_link = pencil_icon.find_parent("a")

    if not edit_link:
        mgr.add_log("❌ Kalem butonu bulunamadı.", "error")
        return False

    edit_btn_id = edit_link.get("id")
        
    # Open modal

    payload_open = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": edit_btn_id,
        "javax.faces.partial.execute": edit_btn_id,
        "javax.faces.partial.render": "addressDialog:addressForm:addressTable", 
        edit_btn_id: edit_btn_id,
        "mainForm": "mainForm",
        **form_data 
    }
    data_rk = ""
    select_btn_id = ""
    xml_data = mgr.session.post(PLAN_URL, data=payload_open)
    vs = extract_viewstate(xml_data.text, vs)
    if vs: current_viewstate = vs

    outer_soup = BeautifulSoup(xml_data.text, 'xml')

    update_tag = outer_soup.find('update', {'id': 'addressDialog:addressForm:addressTable'})

    if update_tag:
        inner_html_content = update_tag.text
        inner_soup = BeautifulSoup(inner_html_content, 'html.parser')

        # Find select button
        
        select_span = inner_soup.find('span', string='Select')
        if select_span:
            # 2. Go up to the parent button
            select_button = select_span.find_parent('button')
            # 3. (Optional) Get the ID to use later
            print(select_button['id'])
            select_btn_id = select_button["id"]
        else:
            print("cant find select buton")
            return None

        target_input = inner_soup.find('input', {'value': location_value})
        
        if target_input:
            parent_tr = target_input.find_parent('tr')
            
            if parent_tr and parent_tr.has_attr('data-rk'):
                print(f"FOUND MATCH!")
                print(f"Row Key (data-rk): {parent_tr['data-rk']}")
                data_rk = parent_tr['data-rk']
                modal_inputs = form_verilerini_topla(inner_html_content)
                payload_select = {
                    "javax.faces.partial.ajax": "true",
                    "javax.faces.source": select_btn_id,
                    "javax.faces.partial.execute": "addressDialog:addressForm", 
                    select_btn_id: select_btn_id,
                    "addressDialog:addressForm": "addressDialog:addressForm", 
                    "addressDialog:addressForm:addressTable_radio": "on", 
                    "addressDialog:addressForm:addressTable_selection": data_rk,
                    "javax.faces.ViewState": current_viewstate,
                    **modal_inputs 
                }
                res_select = mgr.session.post(PLAN_URL, data=payload_select)
                if res_select.status_code == 200:
                    vs_2 = extract_viewstate(res_select.text, vs_2)
                    if vs_2: current_viewstate = vs_2

                    modal_form_data = form_verilerini_topla(inner_html_content)

                    payload_refresh = {
                        "javax.faces.partial.ajax": "true",
                        "javax.faces.source": secret_btn_id,
                        "javax.faces.partial.execute": "@all",
                        "javax.faces.partial.render": "mainForm:draftInfo",
                        secret_btn_id: secret_btn_id,
                        "mainForm": "mainForm",
                        "javax.faces.ViewState": current_viewstate,
                        **modal_form_data
                    }
                    mgr.session.post(PLAN_URL, data=payload_refresh)


            else:
                print("Found input, but parent TR has no data-rk.")
        else:
            print(f"Could not find input with value: {location_value}")

    else:
        print("Could not find the update tag with the table ID.")

def rename_draft_sequence(mgr, target_input_id, new_name, soup_page, current_vs):
    """
    Executes the 2-step rename sequence:
    1. Full Table Update (Request 1)
    2. Specific Change Event (Request 2)
    """
    print(f"🔄 Renaming sequence started for: {new_name}")

    # --- STEP 1: PREPARE PAYLOAD FOR REQUEST #1 (FULL TABLE) ---
    form = soup_page.find("form", id="mainForm")
    if not form: return False

    # Scrape ALL inputs to mimic the browser's full table submission
    payload_req1 = {}
    for tag in form.find_all(["input", "select", "textarea"]):
        name = tag.get("name")
        value = tag.get("value", "")
        if not name: continue
        if tag.get("type") in ["checkbox", "radio"] and not tag.has_attr("checked"):
            continue
        payload_req1[name] = value

    # Overwrite the specific target input with the NEW name
    payload_req1[target_input_id] = new_name
    
    # Add JSF Table Parameters (From your Request 1)
    payload_req1.update({
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": "mainForm:drafts", # Table ID
        "javax.faces.partial.execute": "mainForm:drafts",
        "javax.faces.partial.render": "mainForm:drafts",
        "mainForm:drafts": "mainForm:drafts",
        "mainForm:drafts_encodeFeature": "true",
        "javax.faces.ViewState": current_vs
    })

    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Faces-Request": "partial/ajax",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": DRAFT_PAGE_URL
    }

    try:
        # --- SEND REQUEST #1 ---
        res1 = mgr.session.post(DRAFT_PAGE_URL, data=payload_req1, headers=headers)
        
        if res1.status_code != 200:
            print(f"❌ Request 1 Failed: {res1.status_code}")
            return False

        # IMPORTANT: Capture the NEW ViewState from Request 1 to use in Request 2
        # JSF updates the state after every AJAX request.
        vs = extract_viewstate(res1.text, vs)
        next_viewstate = vs if vs else current_vs
        
        # --- STEP 2: PREPARE PAYLOAD FOR REQUEST #2 (CHANGE EVENT) ---
        payload_req2 = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": target_input_id,
            "javax.faces.partial.execute": target_input_id,
            "javax.faces.behavior.event": "change",
            "javax.faces.partial.event": "change",
            "javax.faces.partial.render": "@none", # Assuming we don't need re-render
            target_input_id: new_name, # The Key must be the Input ID
            "mainForm": "mainForm",
            "javax.faces.ViewState": next_viewstate # Use the FRESH ViewState
        }

        # --- SEND REQUEST #2 ---
        res2 = mgr.session.post(DRAFT_PAGE_URL, data=payload_req2, headers=headers)
        
        if res2.status_code == 200:
            print(f"✅ Rename Sequence Complete: {new_name}")
            return True
        else:
            print(f"❌ Request 2 Failed: {res2.status_code}")
            return False

    except Exception as e:
        print(f"❌ Rename Sequence Error: {e}")
        return False

