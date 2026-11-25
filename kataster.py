# -*- coding: utf-8 -*-
"""Parcel search: GURS WFS, with the user's own layer as a fallback.

No user interface here. Web requests may run in a QgsTask; the fallback layer
is always read on the main thread (it is a QGIS layer).
"""
import csv
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from qgis.PyQt.QtCore import QCoreApplication, QThread, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (
    Qgis, QgsAbstractMetadataBase, QgsApplication, QgsBlockingNetworkRequest, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsExpression, QgsFeature, QgsFeatureRequest, QgsFields, QgsGeometry, QgsJsonUtils,
    QgsLayerMetadata, QgsMemoryProviderUtils, QgsMessageLog, QgsProject, QgsSettings, QgsVectorLayer, QgsWkbTypes,
)

MESSAGE_CATEGORY = "SiKataster"

WFS_URL = "https://ipi.eprostor.gov.si/wfs-si-gurs-kn/wfs"
WFS_PARCEL = "SI.GURS.KN:PARCELE"              # one parcel (search)
WFS_PARCELS = "SI.GURS.KN:OSNOVNI_PARCELE"     # parcel numbers, parcels under an area
WFS_KO = "SI.GURS.KN:KATASTRSKE_OBCINE"
WFS_KO_FIELD, WFS_PARCEL_FIELD = "KO_ID", "ST_PARCELE"
WFS_MAX = 20000
WFS_TIMEOUT_MS = 20000
WFS_CHECK_SECONDS = 300
KO_MAX_AGE_DAYS = 30

CRS = QgsCoordinateReferenceSystem("EPSG:3794")

KEY_LAYER, KEY_SOURCE, KEY_PROVIDER = (
    "SiKataster/fallback/layer", "SiKataster/fallback/source", "SiKataster/fallback/provider")
KEY_KO_FIELD, KEY_PARC_FIELD = "SiKataster/fallback/ko_field", "SiKataster/fallback/parcela_field"


def log(msg, level=None):
    QgsMessageLog.logMessage(str(msg), MESSAGE_CATEGORY, Qgis.MessageLevel.Info if level is None else level)


def enum(owner, scope, name):
    """Enum member that works with both scoped (Qt6) and flat (Qt5) bindings."""
    return getattr(getattr(owner, scope, owner), name)


def plugin_path(name=""):
    return Path(__file__).parent / name


def profile_path(name=""):
    """Folder for SiKataster data in the user's QGIS profile (survives plugin updates)."""
    folder = Path(QgsApplication.qgisSettingsDirPath()) / "sikataster"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name if name else folder


class Result:
    def __init__(self, features, fields, crs, source, authoritative=False, ko_field=None, parcel_field=None,
                 url="", link_type=""):
        self.features = features
        self.fields = fields
        self.crs = crs
        self.source = source                  # text for the status line, e.g. "GURS WFS"
        self.authoritative = authoritative    # True: an empty answer means "does not exist"
        self.ko_field = ko_field
        self.parcel_field = parcel_field
        self.url = url                        # written into the metadata of loaded layers
        self.link_type = link_type

    @property
    def label(self):
        return self.source


# ---------------------------------------------------------------------------
# Web requests - with a timeout; safe to call from background threads.
#
# In background threads QgsBlockingNetworkRequest can deadlock QGIS, so outside
# the main thread plain urllib is used, with the proxy from the QGIS settings
# (or the system proxy) and the system certificates.
# ---------------------------------------------------------------------------
def _is_main_thread():
    app = QCoreApplication.instance()
    return app is None or QThread.currentThread() == app.thread()


def _qgis_proxies():
    """urllib proxies from QGIS → Nastavitve → Omrežje (None = system proxy)."""
    s = QgsSettings()
    if str(s.value("proxy/proxyEnabled", False)).lower() not in ("true", "1"):
        return {}
    kind = str(s.value("proxy/proxyType", "DefaultProxy"))
    if kind == "DefaultProxy":
        return None
    host, port = s.value("proxy/proxyHost", ""), s.value("proxy/proxyPort", "")
    if not host:
        return None
    from urllib.parse import quote
    user, password = s.value("proxy/proxyUser", ""), s.value("proxy/proxyPassword", "")
    login = f"{quote(str(user))}:{quote(str(password))}@" if user else ""
    scheme = "socks5h" if kind == "Socks5Proxy" else "http"
    address = f"{scheme}://{login}{host}" + (f":{port}" if port else "")
    return {"http": address, "https": address}


def _urllib_get(url, timeout_ms):
    import urllib.error
    import urllib.request
    proxies = _qgis_proxies()
    handlers = [] if proxies is None else [urllib.request.ProxyHandler(proxies)]
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={"User-Agent": "QGIS SiKataster"})
    try:
        with opener.open(request, timeout=timeout_ms / 1000) as reply:
            return reply.read()
    except urllib.error.HTTPError as e:
        raise IOError(f"{e.code} {e.reason} – {url}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise IOError(f"Napaka pri dostopu do {url}: {getattr(e, 'reason', e)}")


def http_get(url, timeout_ms=WFS_TIMEOUT_MS):
    if not _is_main_thread():
        return _urllib_get(url, timeout_ms)
    request = QNetworkRequest(QUrl(url))
    if hasattr(request, "setTransferTimeout"):
        request.setTransferTimeout(timeout_ms)
    blocking = QgsBlockingNetworkRequest()
    err = blocking.get(request, True)
    if err != enum(QgsBlockingNetworkRequest, "ErrorCode", "NoError"):
        raise IOError(blocking.errorMessage() or f"Napaka pri dostopu do {url}")
    return bytes(blocking.reply().content())


# ---------------------------------------------------------------------------
# GURS WFS
# ---------------------------------------------------------------------------
def wfs_request_url(type_name, **params):
    base = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": type_name,
            "outputFormat": "application/json"}
    base.update(params)
    return WFS_URL + "?" + urlencode(base)


def _wfs_json(type_name, **params):
    """(parsed JSON, request url)"""
    url = wfs_request_url(type_name, **params)
    text = http_get(url).decode("utf-8")
    try:
        return json.loads(text), url
    except ValueError:
        raise IOError(f"GURS WFS ni vrnil JSON: {' '.join(text[:200].split())}")


def _wfs_features(type_name, **params):
    js, url = _wfs_json(type_name, srsName="EPSG:3794", count=WFS_MAX, **params)
    text = json.dumps(js)
    fields = QgsJsonUtils.stringToFields(text)
    feats = QgsJsonUtils.stringToFeatureList(text, fields)
    return feats, fields, url


_wfs_state = {"ok": None, "when": 0, "error": ""}


def wfs_reachable(refresh=False):
    """One-record query instead of GetCapabilities; remembered for a few minutes."""
    if refresh or _wfs_state["ok"] is None or time.time() - _wfs_state["when"] > WFS_CHECK_SECONDS:
        try:
            _wfs_json(WFS_PARCELS, count=1, propertyName=WFS_KO_FIELD)
            _wfs_state.update(ok=True, error="")
        except IOError as e:
            _wfs_failed(e)
        _wfs_state["when"] = time.time()
    return _wfs_state["ok"]


def _wfs_failed(error):
    """Remember and log the real reason, so the user can see it."""
    _wfs_state.update(ok=False, error=str(error), when=time.time())
    log(f"GURS WFS ni dosegljiv: {error}", Qgis.MessageLevel.Warning)


def wfs_error():
    """Text of the last GURS WFS error ('' if the last request went through)."""
    return _wfs_state["error"]


def wfs_check(task=None):
    """For the 'Preveri' button: (reachable, error text)."""
    return wfs_reachable(refresh=True), wfs_error()


# ---------------------------------------------------------------------------
# K.O. list
# ---------------------------------------------------------------------------
def ko_path():
    p = profile_path("ko.csv")
    return p if p.exists() else plugin_path("ko.csv")


def load_ko():
    """{ko_id (int): name}"""
    try:
        with open(ko_path(), encoding="utf-8") as f:
            return {int(r["KO_ID"]): r["NAZIV"] for r in csv.DictReader(f) if r.get("KO_ID")}
    except (OSError, ValueError, KeyError) as e:
        log(f"Seznama K.O. ni mogoče prebrati: {e}", Qgis.MessageLevel.Warning)
        return {}


def ko_needs_refresh():
    p = profile_path("ko.csv")
    return not p.exists() or (time.time() - p.stat().st_mtime) > KO_MAX_AGE_DAYS * 86400


def refresh_ko(task=None):
    """Download the K.O. list from GURS into the profile (background task)."""
    js, _url = _wfs_json(WFS_KO, propertyName="KO_ID,NAZIV")
    rows = sorted({(int(f["properties"]["KO_ID"]), f["properties"]["NAZIV"]) for f in js.get("features", [])})
    if len(rows) < 2000:                  # Slovenia has ~2700 K.O.; anything less is a broken answer
        raise IOError(f"Seznam K.O. z GURS ni popoln ({len(rows)}).")
    target = profile_path("ko.csv")
    tmp = target.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["KO_ID", "NAZIV"])
        w.writerows(rows)
    tmp.replace(target)
    return len(rows)


def resolve_ko(text, ko):
    """(ko_id, None) for an id or a unique name; (None, [candidates]) otherwise."""
    text = (text or "").strip()
    m = re.match(r"^(\d+)", text)
    if m:
        ko_id = int(m.group(1))
        return (ko_id, None) if ko_id in ko else (None, [])
    matches = [k for k, name in ko.items() if name.lower() == text.lower()]
    if len(matches) == 1:
        return matches[0], None
    if not matches and text:
        matches = [k for k, name in ko.items() if name.lower().startswith(text.lower())]
        if len(matches) == 1:
            return matches[0], None
    return None, sorted(matches)


def ko_label(ko_id, ko):
    return f"{ko_id} - {ko.get(ko_id, '?')}"


def normalize_parcel(text):
    return re.sub(r"\s+", "", text or "")


def parcel_sort_key(p):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", str(p))]


# ---------------------------------------------------------------------------
# Sources: GURS WFS
# ---------------------------------------------------------------------------
def find_wfs(ko_id, parcela):
    parcela = parcela.replace("'", "''")
    feats, fields, url = _wfs_features(
        WFS_PARCEL, CQL_FILTER=f"{WFS_KO_FIELD}={int(ko_id)} AND {WFS_PARCEL_FIELD}='{parcela}'")
    return Result(feats, fields, CRS, "GURS WFS", True, WFS_KO_FIELD, WFS_PARCEL_FIELD, url, "OGC:WFS")


def intersect_wfs(geom):
    b = geom.boundingBox()
    feats, fields, url = _wfs_features(
        WFS_PARCELS, bbox=f"{b.xMinimum()},{b.yMinimum()},{b.xMaximum()},{b.yMaximum()},EPSG:3794")
    if len(feats) >= WFS_MAX:
        raise IOError(f"Območje je preveliko (več kot {WFS_MAX} parcel).")
    return Result(_intersecting(feats, geom), fields, CRS, "GURS WFS", True, WFS_KO_FIELD, WFS_PARCEL_FIELD,
                  url, "OGC:WFS")


def parcel_numbers_wfs(ko_id):
    js, _url = _wfs_json(WFS_PARCELS, propertyName=WFS_PARCEL_FIELD, CQL_FILTER=f"{WFS_KO_FIELD}={int(ko_id)}",
                         count=WFS_MAX)
    return sorted({f["properties"][WFS_PARCEL_FIELD] for f in js.get("features", [])}, key=parcel_sort_key)


# --- for QgsTask.fromFunction: None means "GURS WFS did not answer" ----------
def _remote(query, *args):
    if not wfs_reachable():
        return None
    try:
        return query(*args)
    except IOError as e:
        _wfs_failed(e)
        return None


def find_remote(task, ko_id, parcela):
    return _remote(find_wfs, ko_id, parcela)


def intersect_remote(task, geom):
    return _remote(intersect_wfs, geom)


def parcel_numbers_remote(task, ko_id):
    return _remote(parcel_numbers_wfs, ko_id)


# ---------------------------------------------------------------------------
# Sources: the user's fallback layer (main thread only)
# ---------------------------------------------------------------------------
def _layer_result(layer, request, ko_field=None, parcel_field=None):
    feats = list(layer.getFeatures(request))
    return Result(feats, layer.fields(), layer.crs(), layer.name(), False, ko_field, parcel_field,
                  layer.publicSource(), layer.providerType())


def _equality(layer, field, value, number=False):
    """Expression "field" = value, with the value typed like the field (number or text).
    number=True: a text field may hold the number with leading zeros ("0505" = 505)."""
    idx = layer.fields().indexOf(field)
    if idx < 0:
        raise ValueError(f"Sloj »{layer.name()}« nima polja »{field}«.")
    if layer.fields().at(idx).isNumeric():
        try:
            value = int(value)
        except ValueError:
            return "FALSE"
        return QgsExpression.createFieldEqualityExpression(field, value)
    if number:
        try:
            n = int(value)
        except ValueError:
            return "FALSE"
        variants = sorted({str(n)} | {str(n).zfill(w) for w in range(2, 7)})
        return f"{QgsExpression.quotedColumnRef(field)} IN ({', '.join(QgsExpression.quotedString(v) for v in variants)})"
    return QgsExpression.createFieldEqualityExpression(field, str(value))


def find_layer(layer, ko_field, parcel_field, ko_id, parcela):
    expr = f"{_equality(layer, ko_field, ko_id, number=True)} AND {_equality(layer, parcel_field, parcela)}"
    return _layer_result(layer, QgsFeatureRequest(QgsExpression(expr)), ko_field, parcel_field)


def _intersecting(feats, geom):
    engine = QgsGeometry.createGeometryEngine(geom.constGet())
    engine.prepareGeometry()
    return [f for f in feats if f.hasGeometry() and engine.intersects(f.geometry().constGet())]


def intersect_layer(layer, geom, ko_field=None, parcel_field=None):
    xf = QgsCoordinateTransform(CRS, layer.crs(), QgsProject.instance())
    g = QgsGeometry(geom)
    g.transform(xf)
    request = QgsFeatureRequest()
    request.setFilterRect(g.boundingBox())
    r = _layer_result(layer, request, ko_field, parcel_field)
    r.features = _intersecting(r.features, g)
    return r


def parcel_numbers_layer(layer, ko_field, parcel_field, ko_id):
    req = QgsFeatureRequest(QgsExpression(_equality(layer, ko_field, ko_id, number=True)))
    flags = getattr(Qgis, "FeatureRequestFlag", None)
    req.setFlags(flags.NoGeometry if flags else QgsFeatureRequest.NoGeometry)
    req.setSubsetOfAttributes([parcel_field], layer.fields())
    return sorted({str(f[parcel_field]) for f in layer.getFeatures(req)}, key=parcel_sort_key)


def area_geometry(layer, selected_only=False, buffer_m=0.0):
    """Union of the layer's (selected) features in EPSG:3794, optionally buffered
    (a negative buffer shrinks the area)."""
    feats = layer.selectedFeatures() if selected_only else list(layer.getFeatures())
    geoms = [f.geometry().makeValid() for f in feats if f.hasGeometry()]
    if not geoms:
        raise ValueError("Sloj nima (izbranih) objektov.")
    xf = QgsCoordinateTransform(layer.crs(), CRS, QgsProject.instance())
    for g in geoms:
        g.transform(xf)
    geom = QgsGeometry.unaryUnion(geoms)
    if buffer_m:
        geom = geom.buffer(buffer_m, 8)
        if geom.isNull() or geom.isEmpty() or geom.area() <= 0:
            raise ValueError(f"Z odmikom {buffer_m:g} m od območja ne ostane nič.")
    return geom


# ---------------------------------------------------------------------------
# Fallback layer settings (remembered by source, so it works in any project)
# ---------------------------------------------------------------------------
def fallback_settings():
    """(name, source, provider, ko_field, parcel_field) of the fallback layer ('' where not set)."""
    s = QgsSettings()
    return tuple(s.value(k, "") or "" for k in (KEY_LAYER, KEY_SOURCE, KEY_PROVIDER, KEY_KO_FIELD, KEY_PARC_FIELD))


def can_be_fallback_layer(layer):
    """None if the layer can be remembered, else the reason (text)."""
    if layer is None:
        return "Ni sloja."
    if layer.providerType() in ("memory", "virtual"):
        return "Začasnega sloja ni mogoče shraniti – shranite ga v datoteko ali bazo."
    if not layer.source():
        return "Sloj nima vira."
    return None


def guess_field(fields, kind):
    patterns = {"ko": r"^(sifko|ko_?id|ko|sif_?ko|ko_?sifra|sifra_?ko)$",
                "parcela": r"^(parcela|st_?parcele|parc_?st|stparc|parcelna_?st|st_?parc)$"}[kind]
    for f in fields:
        if re.match(patterns, f.name(), re.I):
            return f.name()
    return ""


def save_fallback_layer(layer, ko_field="", parcel_field=""):
    reason = can_be_fallback_layer(layer)
    if reason:
        raise ValueError(reason)
    s = QgsSettings()
    s.setValue(KEY_LAYER, layer.name())
    s.setValue(KEY_SOURCE, layer.source())
    s.setValue(KEY_PROVIDER, layer.providerType())
    s.setValue(KEY_KO_FIELD, ko_field or guess_field(layer.fields(), "ko"))
    s.setValue(KEY_PARC_FIELD, parcel_field or guess_field(layer.fields(), "parcela"))
    _fallback_cache.clear()


def save_fallback_fields(ko_field, parcel_field):
    s = QgsSettings()
    s.setValue(KEY_KO_FIELD, ko_field or "")
    s.setValue(KEY_PARC_FIELD, parcel_field or "")


def clear_fallback_layer():
    s = QgsSettings()
    for k in (KEY_LAYER, KEY_SOURCE, KEY_PROVIDER, KEY_KO_FIELD, KEY_PARC_FIELD):
        s.remove(k)
    _fallback_cache.clear()


_fallback_cache = {}


def open_fallback_layer():
    """The fallback layer, opened from its saved source (not added to the project), or None."""
    name, source, provider, _, _ = fallback_settings()
    if not source:
        return None
    key = (source, provider)
    layer = _fallback_cache.get(key)
    if layer is None or not layer.isValid():
        layer = QgsVectorLayer(source, name or "rezervni sloj parcel", provider or "ogr")
        if not layer.isValid():
            log(f"Rezervnega sloja parcel ni mogoče odpreti: {name}", Qgis.MessageLevel.Warning)
            return None
        _fallback_cache.clear()
        _fallback_cache[key] = layer
    return layer


def fallback_layer():
    """(layer, ko_field, parcel_field) if the fallback layer is set and can be opened, else None."""
    _, _, _, ko_field, parcel_field = fallback_settings()
    if not (ko_field and parcel_field):
        return None
    layer = open_fallback_layer()
    if layer is not None and layer.fields().indexOf(ko_field) >= 0 and layer.fields().indexOf(parcel_field) >= 0:
        return layer, ko_field, parcel_field
    return None


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def source_metadata(title, abstract, url, link_type):
    """Layer metadata with the source and the time of access; {} in abstract = date and time."""
    now = datetime.now()
    md = QgsLayerMetadata()
    md.setTitle(title)
    md.setAbstract(abstract.replace("{}", f"{now:%d. %m. %Y %H:%M}", 1))
    md.addHistoryItem(str({"timestamp": now.isoformat(), "process": "prevzem sloja", "title": title, "source": url}))
    if url:
        link = QgsAbstractMetadataBase.Link("Vir podatkov", link_type, url)
        link.description = f"Dostop: {now}"
        md.addLink(link)
    return md


def to_memory_layer(result, name):
    """Copy the result into a temporary layer; source and time of access go into its metadata."""
    geoms = [f.geometry() for f in result.features if f.hasGeometry()]
    default = getattr(Qgis, "WkbType", QgsWkbTypes).MultiPolygon
    wkb = QgsWkbTypes.multiType(geoms[0].wkbType()) if geoms else default
    fields = result.fields if result.fields is not None else QgsFields()
    layer = QgsMemoryProviderUtils.createMemoryLayer(name, fields, wkb, result.crs)
    feats = []
    for f in result.features:
        g = QgsGeometry(f.geometry())
        g.convertToMultiType()
        nf = QgsFeature(fields)
        nf.setAttributes(f.attributes()[:fields.count()])
        nf.setGeometry(g)
        feats.append(nf)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()

    layer.setMetadata(source_metadata(name, "Pridobljeno {}, vir: " + result.label + ".", result.url, result.link_type))
    return layer


# ---------------------------------------------------------------------------
# 'Naloži KN': live GURS WFS layers for the whole country, in one group
# ---------------------------------------------------------------------------
KN_GROUP = "Kataster nepremičnin"
KN_LAYERS = [                           # (WFS type, layer name, style file) - top to bottom in the group
    (WFS_KO, "Katastrske občine", "katastrske_obcine.qml"),
    (WFS_PARCELS, "Parcele", "parcele.qml"),
]


def wfs_layer_uri(type_name):
    return (f"pagingEnabled='true' pageSize='{WFS_MAX}' preferCoordinatesForWfsT11='false' "
            f"restrictToRequestBBOX='1' srsname='EPSG:3794' typename='{type_name}' url='{WFS_URL}' version='2.0.0'")


def _kn_layer_in_project(type_name):
    for layer in QgsProject.instance().mapLayers().values():
        source = layer.source()
        if layer.providerType() == "WFS" and WFS_URL in source and f"typename='{type_name}'" in source:
            return layer
    return None


def load_kn_layers():
    """Add the K.O. and parcel WFS layers to the group 'Kataster nepremičnin' (main thread only).
    Layers already in the project are left alone. Returns (added names, names already there);
    IOError if a layer cannot be opened - then nothing is added."""
    added, present, new_layers = [], [], []
    for type_name, name, style in KN_LAYERS:
        if _kn_layer_in_project(type_name) is not None:
            present.append(name)
            continue
        layer = QgsVectorLayer(wfs_layer_uri(type_name), name, "WFS")
        if not layer.isValid():
            reason = " ".join(layer.error().summary().split())
            raise IOError(f"Sloja »{name}« ni mogoče naložiti iz GURS WFS." + (f" {reason}" if reason else ""))
        message, ok = layer.loadNamedStyle(str(plugin_path("styles") / style))
        if not ok:
            log(f"Sloga {style} ni mogoče uporabiti: {message}", Qgis.MessageLevel.Warning)
        layer.setMetadata(source_metadata(name, "Sloj dodan {}, vir: GURS WFS (podatki se berejo sproti).",
                                          layer.publicSource(), "OGC:WFS"))
        new_layers.append(layer)
        added.append(name)
    if new_layers:
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group = root.findGroup(KN_GROUP) or root.insertGroup(0, KN_GROUP)
        for layer in new_layers:
            project.addMapLayer(layer, False)
            group.addLayer(layer)
    return added, present


def zoom_and_flash(canvas, result):
    geoms = [f.geometry() for f in result.features if f.hasGeometry()]
    if not geoms:
        return
    xf = QgsCoordinateTransform(result.crs, canvas.mapSettings().destinationCrs(), QgsProject.instance())
    box = xf.transformBoundingBox(QgsGeometry.unaryUnion(geoms).boundingBox())
    box.scale(1.5)
    canvas.setExtent(box)
    canvas.refresh()
    canvas.flashGeometries(geoms, result.crs)
