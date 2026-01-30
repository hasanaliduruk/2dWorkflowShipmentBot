from bs4 import BeautifulSoup
import requests
import re

from bot.jsf import form_verilerini_topla
from bot.constants import (
    LOGIN_URL,
    DRAFT_PAGE_URL,
)

def login(mgr):
    """Siteye giriş yapar."""

    try:
        # Önce login sayfasına gidip ViewState alalım

        mgr.session.cookies.clear()

        res = mgr.session.get(LOGIN_URL)
        soup = BeautifulSoup(res.text, 'html.parser')
        view_state_input = soup.find("input", {"name": "javax.faces.ViewState"})
        button_id = soup.find("button").get("id")

        if not view_state_input:
            print("HATA: Login sayfasında ViewState bulunamadı.")
            return False
        view_state = view_state_input.get('value')

        payload = {
            "mainForm": "mainForm",
            "mainForm:email": mgr.email,
            "mainForm:password": mgr.password,
            button_id: "",
            "javax.faces.ViewState": view_state
        }

        post_res = mgr.session.post(LOGIN_URL, data=payload, headers={"Referer": LOGIN_URL})

        # Başarılı login kontrolü:
        # JSF genelde hata verirse aynı sayfada kalır, başarırsa redirect eder.
        # URL hala login.jsf ise veya içerikte hata mesajı varsa başarısızdır.
        if "login.jsf" in post_res.url and "ui-messages-error" in post_res.text:
            print("Login Başarısız: Hata mesajı algılandı.")
            return False
        print(f"Login isteği sonucu: {post_res.status_code}, URL: {post_res.url}")

        fetch_accounts_backend(mgr, DRAFT_PAGE_URL)

        return True

    except Exception as e:
        print(f"Login işlem hatası: {e}")

        return False

def fetch_accounts_backend(mgr, current_url=DRAFT_PAGE_URL):
    """
    1. Gets the current page to find out who we are logged in as (ccFlag).
    2. Opens the menu to get the list of available accounts.
    """
    try:
        # --- ADIM 1: MEVCUT HESABI BUL (GET İSTEĞİ) ---
        res_page = mgr.session.get(current_url)
        # Login ekranına attıysa dur
        if "login.jsf" in res_page.url: 
            print("Login gerekli.")
            return False

        soup_page = BeautifulSoup(res_page.text, 'html.parser')
        
        # Sayfanın tepesindeki bayrak/isim alanını bul (id="ccFlag")
        active_account_name = "Bilinmiyor"
        cc_flag_div = soup_page.find("div", id="ccFlag")
        
        if cc_flag_div:
            # Span içindeki texti al (örn: " Babil Design")
            span_text = cc_flag_div.get_text(strip=True)
            if span_text:
                active_account_name = span_text
                mgr.current_account_name = active_account_name
                print(f"✅ Aktif Hesap Tespit Edildi: {active_account_name}")
        else:
            print("⚠️ ccFlag bulunamadı, aktif hesap adı çekilemedi.")

        # --- ADIM 2: HESAP LİSTESİNİ ÇEK (POST İSTEĞİ) ---
        # Menu butonuna basıp listeyi alıyoruz
        form_data = form_verilerini_topla(res_page.text)
        menu_btn_id = None
        
        # Strategy B: Fallback to onclick content if A fails
        if not menu_btn_id:
            link = soup_page.find("a", onclick=re.compile(r"__my_store__"))
            if link: menu_btn_id = link.get("id")

        # Strategy A: Look for Amazon Icon
        icon = soup_page.find("i", class_="fa-amazon")
        if icon:
            parent = icon.find_parent("a")
            if parent: menu_btn_id = parent.get("id")
            
        if not menu_btn_id:
            print("❌ Could not find the Account Menu button ID.")
            return False
        
        payload = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": menu_btn_id,
            "javax.faces.partial.execute": "@all",
            "javax.faces.partial.render": "__my_store_form__:__my_stor_table__",
            menu_btn_id: menu_btn_id,
            "formLogo": "formLogo",
            "javax.faces.ViewState": form_data.get("javax.faces.ViewState", "")
        }
        
        res_menu = mgr.session.post(current_url, data=payload)
        
        # XML Parse
        outer_soup = BeautifulSoup(res_menu.text, 'xml')
        update_tag = outer_soup.find('update', {'id': '__my_store_form__:__my_stor_table__'})
        
        if not update_tag:
            print("Hesap tablosu XML içinde bulunamadı.")
            return False

        inner_html = update_tag.text
        inner_soup = BeautifulSoup(inner_html, 'html.parser')
        rows = inner_soup.find_all("tr", attrs={"data-rk": True})
        
        new_accounts_list = []
        
        for row in rows:
            rk_id = row['data-rk']
            
            # İsmi input değerinden al
            name_input = row.find("input", id=lambda x: x and "store_name" in x)
            name = name_input['value'] if name_input else row.get_text(strip=True)
            
            # --- AKTİFLİK KONTROLÜ ---
            # Tablodaki isim ile yukarıda bulduğumuz aktif isim aynı mı?
            # (Küçük/büyük harf duyarlılığını kaldırmak için .strip() kullanıyoruz)
            is_active = (name.strip() == active_account_name.strip())
            if is_active:
                mgr.current_account_id = rk_id
            new_accounts_list.append({
                "id": rk_id,
                "name": name,
                "flag": "🇺🇸", 
                "is_active": is_active
            })
            
        mgr.available_accounts = new_accounts_list
        return True

    except Exception as e:
        print(f"Hesap çekme hatası: {e}")
        return False

def switch_account_backend(mgr, account_rk, current_url=DRAFT_PAGE_URL):
    """
    Switches the account using the row key (data-rk).
    """
    try:
        mgr.add_log("Hesap değiştiriliyor...", "info")
        
        # We need the current ViewState and also the form data from the account list 
        # (because JSF often requires the values of the inputs in the table to be sent back)
        
        # 1. Trigger fetch again to ensure we have the latest table state/ViewState to submit
        # Or simply use the page we are on. Let's assume we are on DRAFT_PAGE_URL.
        res_page = mgr.session.get(current_url)
        form_data = form_verilerini_topla(res_page.text)
        
        # We need to construct the specific payload for row selection
        # Note: We need to recreate the inputs for the table rows (store_name) 
        # usually found in the form data if the modal was rendered.
        
        # Since the modal might not be in the DOM of the main page GET request, 
        # we might need to manually construct the minimal payload.
        
        payload = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "__my_store_form__:__my_stor_table__",
            "javax.faces.partial.execute": "__my_store_form__:__my_stor_table__",
            "javax.faces.partial.render": "ccFlag contentPanel mainForm menuform",
            "javax.faces.behavior.event": "rowSelect",
            "javax.faces.partial.event": "rowSelect",
            "__my_store_form__:__my_stor_table___instantSelectedRowKey": account_rk,
            "__my_store_form__": "__my_store_form__",
            "__my_store_form__:__my_stor_table__:j_idt26:filter": "",
            "__my_store_form__:__my_stor_table___selection": account_rk,
            "__my_store_form__:__my_stor_table___scrollState": "0,0",
            "javax.faces.ViewState": form_data.get("javax.faces.ViewState", "")
        }
        
        # Sending request
        res = mgr.session.post(current_url, data=payload)
        
        # Check for success (Look for ccFlag update which shows the new name)
        if "update id=\"ccFlag\"" in res.text:
            # Refresh accounts list to update 'active' status in our UI
            fetch_accounts_backend(mgr) 
            mgr.add_log("✅ Hesap başarıyla değiştirildi.", "success")
            return True
        else:
            mgr.add_log("❌ Hesap değiştirme başarısız oldu.", "error")
            return False
            
    except Exception as e:
        mgr.add_log(f"Switch error: {e}", "error")
        return False