"""Human-readable invoice history (timeline) built from InvoiceAuditLog rows."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from django.utils import timezone

from apps.billing.models import Invoice, InvoiceAuditLog
from apps.ui.templatetags.ui_format import eur, isodatetime

A = InvoiceAuditLog.Action
STATUS_LABELS = dict(Invoice.Status.choices)

# CDAR issuer roles (UNTDID 3035 subset used by the French lifecycle)
ISSUER_ROLES = {
    "SE": "Vendeur",
    "BY": "Acheteur",
    "WK": "Plateforme agréée",
    "DFH": "Administration fiscale",
}

SOURCES = {
    "celery_task": "Traitement automatique",
    "cdar_poll": "Cycle de vie remonté par la plateforme agréée",
    "afnor_flow_fallback": "Statut du flux AFNOR",
    "prepaid_auto": (
        "Paiement reçu avant l'émission, appliqué automatiquement dès la transmission"
    ),
    "online_payment": "Paiement en ligne confirmé",
}

REASONS = {
    "cancel": "Annulation par avoir",
    "credit_note_validated": "Avoir validé",
    "missing buyer siren and electronic address": (
        "SIREN et adresse électronique de l'acheteur manquants"
    ),
    "missing supplier siren": "SIREN du fournisseur manquant",
}

# Labels for detail keys not handled explicitly below
KEY_LABELS = {
    "flow_id": "Identifiant du flux",
    "document_id": "Identifiant du message",
    "status": "Statut renvoyé",
    "flux_type": "Type de flux",
    "error": "Erreur",
    "error_type": "Type d'erreur",
    "reason": "Raison",
    "field": "Champ",
    "item": "Élément",
    "payment_date": "Date de paiement",
    "payment_reference": "Référence de paiement",
    "amount": "Montant",
    "preceding_invoice": "Facture d'origine",
    "credit_note": "Avoir",
}

# Keys already rendered by a dedicated rule (or purely technical)
HIDDEN_KEYS = {
    "seller_id",
    "invoice_id",
    "status_code",
    "status_description",
    "at",
    "issuer_siren",
    "issuer_role",
    "reason_code",
    "source",
    "cdar_status_code",
    "afnor_flow",
    "flow_direction",
}


@dataclass
class TimelineEntry:
    title: str
    timestamp: object
    subtitle: str = ""
    tone: str = ""  # "", "success", "error", "muted"
    rows: list = field(default_factory=list)  # [(label, value, is_code)]


def _french_date(value):
    """'2026-09-21' -> '21/09/2026' (left untouched if not an ISO date)."""
    try:
        return date.fromisoformat(str(value)).strftime("%d/%m/%Y")
    except ValueError:
        return value


def _status(code):
    return STATUS_LABELS.get(code, code or "—")


def _row(rows, label, value, code=False):
    if value not in (None, "", [], {}):
        rows.append((label, str(value), code))


def _status_datetime(value, received_at):
    """CDAR status date (format 204, no time zone) as an aware datetime.

    Emitters disagree: some write UTC, others French local time. Read it as
    UTC, unless that would put it after we received it: then it was local time.
    """
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_aware(parsed):
        return parsed
    as_utc = parsed.replace(tzinfo=UTC)
    if received_at and as_utc > received_at:
        return timezone.make_aware(parsed)
    return as_utc


def _cdar_rows(details, rows, received_at=None):
    code = details.get("status_code", "")
    description = details.get("status_description", "")
    _row(rows, "Statut CDAR", f"{code} — {description}" if description else code)
    at = details.get("at")
    status_dt = _status_datetime(at, received_at) if at else None
    _row(
        rows,
        "Date du statut",
        timezone.localtime(status_dt).strftime("%d/%m/%Y à %H:%M")
        if status_dt
        else isodatetime(at)
        if at
        else "",
    )
    role = details.get("issuer_role", "")
    issuer = details.get("issuer_siren", "")
    if issuer or role:
        role_label = ISSUER_ROLES.get(role, role)
        _row(
            rows,
            "Émis par",
            f"{issuer} ({role_label})"
            if role_label and issuer
            else issuer or role_label,
        )
    _row(rows, "Motif", details.get("reason_code"))
    if details.get("amount") not in (None, ""):
        _row(rows, "Montant", eur(details.get("amount")))


def _is_outgoing(details):
    """Status sent by the seller (us), not received: flow direction, else issuer role."""
    direction = details.get("flow_direction", "")
    if direction:
        return direction.lower() == "out"
    return details.get("issuer_role") == "SE"


def _echo_of(log, submitted):
    """The submitted 212 that a polled CDAR event merely reflects back, or None."""
    details = log.details if isinstance(log.details, dict) else {}
    if log.action != A.CDAR_EVENT or details.get("status_code") != "212":
        return None
    if not _is_outgoing(details):
        return None
    document_id = details.get("document_id")
    for sent in submitted:
        if not document_id or (sent.details or {}).get("document_id") == document_id:
            return sent
    return None


def build_entry(log):
    details = log.details if isinstance(log.details, dict) else {}
    rows = []
    entry = TimelineEntry(title=log.get_action_display(), timestamp=log.timestamp)

    if log.action == A.CDAR_EVENT:
        code = details.get("status_code", "")
        verb = "émis" if _is_outgoing(details) else "reçu"
        entry.title = f"Statut {code} {verb}" if code else f"Statut CDAR {verb}"
        entry.subtitle = details.get("status_description", "")
        if code in ("210", "213"):
            entry.tone = "error"
        _cdar_rows(details, rows, log.timestamp)
    elif log.action == A.CDAR_PAID_SUBMITTED:
        entry.title = "Statut 212 émis"
        entry.subtitle = "Encaissée"
        entry.tone = "success"
    elif log.action == A.CDAR_PAID_ERROR:
        entry.title = "Échec d'envoi du statut 212 (Encaissée)"
        entry.subtitle = (
            "La facture reste payée ; le statut n'a pas été transmis à la plateforme."
        )
        entry.tone = "error"
    elif log.action == A.CDAR_PAID_SKIPPED:
        entry.title = "Statut 212 (Encaissée) non envoyé"
        entry.tone = "muted"
    elif log.action == A.STATUS_CHANGE:
        entry.title = f"{_status(log.old_status)} → {_status(log.new_status)}"
        if log.new_status in (Invoice.Status.REJECTED, Invoice.Status.REFUSED):
            entry.tone = "error"
        source = details.get("source", "")
        if source:
            label = SOURCES.get(source, source)
            if details.get("cdar_status_code"):
                label += f" (statut {details['cdar_status_code']})"
            _row(rows, "Origine", label)
    elif log.action == A.FLOW_SUBMITTED:
        entry.title = "Flux soumis à la plateforme agréée"
    elif log.action == A.CREATED:
        entry.title = "Création"
    elif log.action == A.DATA_UPDATE:
        entry.title = "Modification"
    elif log.action == A.DELETE:
        entry.title = "Suppression"
    elif log.action == A.EREPORTING_SUBMITTED:
        entry.title = f"E-reporting soumis (flux {details.get('flux_type', '?')})"
        entry.tone = "success"
    elif log.action == A.EREPORTING_ERROR:
        entry.title = "Erreur e-reporting"
        entry.tone = "error"

    for key, value in details.items():
        if key in HIDDEN_KEYS or value in (None, "", [], {}):
            continue
        if key == "amount" and log.action == A.CDAR_EVENT:
            continue  # already shown by _cdar_rows
        if key == "reason":
            value = REASONS.get(value, value)
        elif key == "amount":
            value = eur(value)
        elif key == "payment_date":
            value = _french_date(value)
        elif isinstance(value, (dict, list)):
            continue
        _row(
            rows,
            KEY_LABELS.get(key, key.replace("_", " ").capitalize()),
            value,
            code=key in ("flow_id", "document_id", "error"),
        )

    if log.user_id:
        _row(
            rows, "Par", log.user.get_full_name() or log.user.email or log.user.username
        )
    if log.timestamp:
        _row(
            rows,
            "Horodatage",
            timezone.localtime(log.timestamp).strftime("%d/%m/%Y %H:%M:%S"),
        )

    entry.rows = rows
    return entry


def _chronological_key(log):
    """Events recorded in the same second (one CDAR poll) in lifecycle order:
    received statuses by code (200, 201, 202…), then the status changes they caused.
    """
    details = log.details if isinstance(log.details, dict) else {}
    second = log.timestamp.replace(microsecond=0) if log.timestamp else None
    if log.action == A.CDAR_EVENT:
        code = str(details.get("status_code", ""))
        return (second, 0, int(code) if code.isdigit() else 999, log.timestamp)
    return (second, 1, 0, log.timestamp)


def build_timeline(audit_logs):
    """Newest first, with same-second events in lifecycle order.

    The 212 we submitted comes back in the CDAR poll: both are the same flow,
    so the echo is folded into the "émis" entry instead of shown twice.
    """
    logs = [log for log in audit_logs if log.timestamp]
    logs.sort(key=_chronological_key, reverse=True)
    submitted = [log for log in logs if log.action == A.CDAR_PAID_SUBMITTED]
    echoes = {}
    for log in logs:
        sent = _echo_of(log, submitted)
        if sent:
            echoes.setdefault(sent.pk, log)

    entries = []
    for log in logs:
        if any(echo.pk == log.pk for echo in echoes.values()):
            continue
        entry = build_entry(log)
        echo = echoes.get(log.pk)
        if echo:
            amount = (echo.details or {}).get("amount")
            if amount not in (None, ""):
                entry.rows.insert(0, ("Montant", eur(amount), False))
            entry.rows.insert(
                0,
                (
                    "Confirmé par la plateforme",
                    timezone.localtime(echo.timestamp).strftime("%d/%m/%Y à %H:%M"),
                    False,
                ),
            )
        entries.append(entry)
    return entries


# CDAR lifecycle (BR-FR-CDV-CL-06): main path always shown, other statuses when received
LIFECYCLE_LABELS = {
    "200": "Déposée",
    "201": "Émise",
    "202": "Reçue",
    "203": "Mise à disposition",
    "204": "Prise en charge",
    "205": "Approuvée",
    "206": "Approuvée partiellement",
    "207": "En litige",
    "208": "Suspendue",
    "209": "Complétée",
    "210": "Refusée",
    "211": "Paiement transmis",
    "212": "Encaissée",
    "213": "Rejetée",
}
LIFECYCLE_MAIN_PATH = ("200", "202", "203", "212")
LIFECYCLE_STOPS = {"210", "213"}  # refused / rejected: nothing comes after
LIFECYCLE_WARNINGS = {"207", "208"}  # disputed / suspended


def lifecycle_steps(invoice):
    """One-line lifecycle summary: [{"code", "label", "state"}] (done/pending/warn/error).

    None when the invoice never reached a platform (draft, e-reporting only…).
    """
    logs = invoice.audit_logs.filter(
        action__in=[A.CDAR_EVENT, A.CDAR_PAID_SUBMITTED, A.FLOW_SUBMITTED]
    ).values_list("action", "details")
    reached = set()
    submitted = False
    for action, details in logs:
        if action == A.FLOW_SUBMITTED:
            submitted = True
        elif action == A.CDAR_PAID_SUBMITTED:
            reached.add("212")
        else:
            code = str((details or {}).get("status_code", ""))
            if code in LIFECYCLE_LABELS:
                reached.add(code)
    if not reached and not submitted:
        return None

    stop = bool(reached & LIFECYCLE_STOPS)
    codes = sorted(reached | set(LIFECYCLE_MAIN_PATH), key=int)
    steps = []
    for code in codes:
        if code not in reached and (stop or code in LIFECYCLE_STOPS):
            continue  # refused / rejected: no step is still expected
        if code in reached:
            state = (
                "error"
                if code in LIFECYCLE_STOPS
                else "warn"
                if code in LIFECYCLE_WARNINGS
                else "done"
            )
        else:
            state = "pending"
        steps.append({"code": code, "label": LIFECYCLE_LABELS[code], "state": state})
    return steps
