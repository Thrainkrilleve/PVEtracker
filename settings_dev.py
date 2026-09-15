# Minimal Django settings for running management commands / tests locally.
# Not used in production — the Alliance Auth install has its own settings.

SECRET_KEY = 'dev-only-not-for-production'
DEBUG = True

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'django.contrib.admin',
    'django.contrib.messages',
    'django.contrib.sessions',
    'django.contrib.staticfiles',
    'allianceauth',
    'allianceauth.authentication',
    'allianceauth.eveonline',
    'allianceauth.services',
    'allianceauth.framework',
    'esi',
    'aa_pvetracker',
]

MIDDLEWARE = [
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

# allianceauth.authentication's task_statistics hook up a redis-backed event
# counter at app-ready() time regardless of whether Celery is in use — point
# it at a redis:// URL so django_redis can build a (lazy) client without
# needing a real Redis server for `manage.py check` / `test` to work.
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://localhost:6379/0',
    }
}

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    }
]

STATIC_URL = '/static/'
ROOT_URLCONF = 'urls_dev'
