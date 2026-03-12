from django.conf import settings

collect_ignore_glob = [] if getattr(settings, "STRIPE_ENABLED", False) else ["test*.py"]
