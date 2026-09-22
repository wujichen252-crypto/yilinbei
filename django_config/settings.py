"""Environment-driven Django 3.2 settings for the Laravel-compatible backend."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be set in the environment")
DEBUG = os.getenv("DEBUG", "False").lower() in {"1", "true", "yes"}
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
TIME_ZONE = os.getenv("TIME_ZONE", "Asia/Shanghai")
USE_I18N = True
USE_L10N = True
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "apps.core",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.TokenCleanupMiddleware",
    "apps.core.middleware.AuditRequestMiddleware",
]
ROOT_URLCONF = "django_config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "django_config.wsgi.application"
ASGI_APPLICATION = "django_config.asgi.application"

DB_ENGINE = os.getenv("DB_ENGINE", "django.db.backends.postgresql")
DATABASES = {"default": {
    "ENGINE": DB_ENGINE,
    "NAME": os.getenv("DB_NAME", "ylb"),
    "USER": os.getenv("DB_USER", "postgres"),
    "PASSWORD": os.getenv("DB_PASSWORD", ""),
    "HOST": os.getenv("DB_HOST", "127.0.0.1"),
    "PORT": os.getenv("DB_PORT", "5432"),
    "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
}}
if os.getenv("GAUSSDB_HOST") and os.getenv("DB_ENGINE", "").lower().find("gauss") >= 0:
    DATABASES["default"] = {
        "ENGINE": os.getenv("GAUSSDB_ENGINE", "django.db.backends.postgresql"),
        "NAME": os.getenv("GAUSSDB_NAME", ""), "USER": os.getenv("GAUSSDB_USER", ""),
        "PASSWORD": os.getenv("GAUSSDB_PASSWORD", ""), "HOST": os.getenv("GAUSSDB_HOST", ""),
        "PORT": os.getenv("GAUSSDB_PORT", "8000"),
        "OPTIONS": json.loads(os.getenv("GAUSSDB_OPTIONS", "{}")),
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
    }

AUTH_USER_MODEL = "core.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
LANGUAGE_CODE = "zh-hans"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / os.getenv("STATIC_ROOT", "staticfiles")
MEDIA_URL = os.getenv("MEDIA_URL", "/media/")
MEDIA_ROOT = BASE_DIR / os.getenv("MEDIA_ROOT", "media")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_BROWSER_XSS_FILTER = True
SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "False").lower() == "true"
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
CORS_ALLOWED_ORIGINS = [x.strip() for x in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if x.strip()]
CORS_ALLOWED_ORIGIN_REGEXES = [x.strip() for x in os.getenv("CORS_ALLOWED_ORIGIN_REGEXES", "").split(",") if x.strip()]
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if x.strip()]
# 默认白名单之外，放行前端为绕过 ngrok 免费版浏览器警告页而带的请求头
# （前端在 axios 拦截器统一加 ngrok-skip-browser-warning: 1）。
from corsheaders.defaults import default_headers  # noqa: E402
CORS_ALLOW_HEADERS = list(default_headers) + ["ngrok-skip-browser-warning"]

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
QINIU_ACCESS_KEY = os.getenv("QINIU_ACCESS_KEY", "")
QINIU_SECRET_KEY = os.getenv("QINIU_SECRET_KEY", "")
QINIU_BUCKET = os.getenv("QINIU_BUCKET", "")
QINIU_DOMAIN = os.getenv("QINIU_DOMAIN", "")

# --- 阿里云 OSS（STS 直传，见 apps/api/views.py 的 oss_token）---
# 长期密钥只存后端环境变量，前端只拿 AssumeRole 签发的短期凭证。
ALIYUN_OSS_ACCESS_KEY_ID = os.getenv("ALIYUN_OSS_ACCESS_KEY_ID", "")
ALIYUN_OSS_ACCESS_KEY_SECRET = os.getenv("ALIYUN_OSS_ACCESS_KEY_SECRET", "")
ALIYUN_OSS_BUCKET = os.getenv("ALIYUN_OSS_BUCKET", "")
ALIYUN_OSS_REGION = os.getenv("ALIYUN_OSS_REGION", "oss-cn-chengdu")
ALIYUN_OSS_ENDPOINT = os.getenv("ALIYUN_OSS_ENDPOINT", "oss-cn-chengdu.aliyuncs.com")
ALIYUN_OSS_HOST = os.getenv("ALIYUN_OSS_HOST", "")  # 对外访问域名，前端拼 URL 用；留空则按 bucket+endpoint 推导
ALIYUN_OSS_STS_ROLE_ARN = os.getenv("ALIYUN_OSS_STS_ROLE_ARN", "")
ALIYUN_OSS_STS_EXPIRE = int(os.getenv("ALIYUN_OSS_STS_EXPIRE", "3600"))

TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", "4"))

LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"default": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "default"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
