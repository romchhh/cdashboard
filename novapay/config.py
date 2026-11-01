# Re-export з єдиного конфігу проєкту

from config import (
    NOVAPAY_TOKENS,
    SESSION_SUFFIX,
    SESSIONS_DIR,
    get_session_file,
    WSDL_URL,
    SESSION_VALIDITY_HOURS,
    REFRESH_BEFORE_HOURS,
    LOG_API_RESPONSES,
    NOVAPAY_USER_KEY,
)

USER_KEY = NOVAPAY_USER_KEY
SESSION_FILE = get_session_file()
