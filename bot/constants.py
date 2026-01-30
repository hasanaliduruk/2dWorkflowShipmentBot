
BASE_URL = "https://app.2dworkflow.com"

LOGIN_URL = f"{BASE_URL}/login.jsf"
DRAFT_PAGE_URL = f"{BASE_URL}/draft.jsf"
PLAN_URL = f"{BASE_URL}/draftplan.jsf"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# JSF Common Keys
JSF_VIEWSTATE = "javax.faces.ViewState"
JSF_PARTIAL = "javax.faces.partial.ajax"
JSF_SOURCE = "javax.faces.source"
JSF_EXECUTE = "javax.faces.partial.execute"
JSF_RENDER = "javax.faces.partial.render"