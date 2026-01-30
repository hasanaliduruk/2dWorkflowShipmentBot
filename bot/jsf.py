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
