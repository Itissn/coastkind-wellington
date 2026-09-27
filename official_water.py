"""Read the latest council laboratory sample; never turn it into a swim advisory.

Public API documentation: https://hilltop.gw.govt.nz/data.hts
Station names/coordinates verified on 2026-09-26 from the council's SiteList:
https://hilltop.gw.govt.nz/data.hts?Service=Hilltop&Request=SiteList&Location=LatLong
The fixed mapping identifies a coastal monitoring station, not the entire bay.
LAWA guidance remains separate: https://www.lawa.org.nz/explore-data/swimming
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
import re
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ENDPOINT = 'https://hilltop.gw.govt.nz/data.hts'
SOURCE_URL = 'https://graphs.gw.govt.nz/envmon?view=map'
LAWA_URL = 'https://www.lawa.org.nz/explore-data/swimming'
PARAMETER = 'Enterococci Bacteria'
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 8
NS = {'wml2': 'http://www.opengis.net/waterml/2.0', 'om': 'http://www.opengis.net/om/2.0'}
XLINK_TITLE = '{http://www.w3.org/1999/xlink}title'

# (exact public station name, published latitude, published longitude).
# Two stations have no coordinates in the official catalogue. Do not fill these
# with an approximate community pin or assign a neighbouring station's samples.
STATIONS = {
    'Oriental Bay': ('Oriental Bay at Band Rotunda', -41.29109602, 174.79432712),
    'Lyall Bay': ('Lyall Bay at Tirangi Road', -41.32832071, 174.80137072),
    'Island Bay': ('Island Bay at Surf Club', -41.34352595, 174.77346379),
    'Porirua Harbour': ('Porirua Harbour at Rowing Club', -41.11400634, 174.84485321),
    'Petone Beach': ('Petone Beach at Kiosk', -41.23250870, 174.88916682),
    'Scorching Bay': ('Scorching Bay', -41.29703432, 174.83359256),
    'Worser Bay': ('Worser Bay', -41.31354924, 174.82876317),
    'Seatoun Beach': ('Seatoun Beach at Wharf', -41.31883265, 174.82956800),
    'Days Bay': ('Days Bay at Wharf', -41.28084503, 174.90641735),
    'Rona Bay': ('Rona Bay at Wharf', -41.28950787, 174.89563525),
    'Mākara Beach': ('Makara Beach', None, None),
    'Tītahi Bay': ('Titahi Bay at Toms Road', -41.10596177, 174.83532731),
    'Plimmerton Beach': ('Plimmerton Beach at Bath Street', -41.08332610, 174.86560139),
    'Paremata': ('Pauatahanui Inlet at Paremata Bridge', -41.10153450, 174.87143953),
    'Pukerua Bay': ('Pukerua Bay', -41.02936295, 174.89481296),
    'Houghton Bay': ('Houghton Bay', None, None),
    'Ōwhiro Bay': ('Owhiro Bay', -41.34489952, 174.75850109),
    'Princess Bay': ('Princess Bay', -41.34407669, 174.78792873),
    'Hataitai Beach': ('Hataitai Beach', -41.30583398, 174.79937783),
    'Breaker Bay': ('Breaker Bay', -41.33017782, 174.83207222),
}

# Approximate community map pins, used only to describe station distance.
COMMUNITY_POINTS = {
    'Oriental Bay': (-41.2911, 174.7943), 'Lyall Bay': (-41.3294, 174.7959),
    'Island Bay': (-41.3435, 174.7735), 'Porirua Harbour': (-41.114, 174.8449),
    'Petone Beach': (-41.2325, 174.8892), 'Scorching Bay': (-41.297, 174.8336),
    'Worser Bay': (-41.3135, 174.8288), 'Seatoun Beach': (-41.3188, 174.8296),
    'Days Bay': (-41.2808, 174.9064), 'Rona Bay': (-41.2895, 174.8956),
    'Mākara Beach': (-41.2202, 174.7126), 'Tītahi Bay': (-41.106, 174.8353),
    'Plimmerton Beach': (-41.0833, 174.8656), 'Paremata': (-41.1015, 174.8714),
    'Pukerua Bay': (-41.0292, 174.892), 'Houghton Bay': (-41.3437, 174.7853),
    'Ōwhiro Bay': (-41.3449, 174.7585), 'Princess Bay': (-41.3441, 174.7879),
    'Hataitai Beach': (-41.3058, 174.7994), 'Breaker Bay': (-41.3302, 174.8321),
}


def _distance(point, latitude, longitude):
    if latitude is None or longitude is None:
        return None
    start_lat, end_lat = map(math.radians, (point[0], latitude))
    delta_lat = end_lat - start_lat
    delta_lon = math.radians(longitude - point[1])
    angle = math.sin(delta_lat / 2) ** 2 + math.cos(start_lat) * math.cos(end_lat) * math.sin(delta_lon / 2) ** 2
    return round(6371.0088 * 2 * math.asin(min(1, math.sqrt(angle))), 3)


def metadata(community):
    if not isinstance(community, str) or community not in STATIONS:
        raise ValueError('Choose a supported coastal community.')
    station, latitude, longitude = STATIONS[community]
    station_url = ENDPOINT + '?' + urlencode({
        'Service': 'SOS', 'Request': 'GetObservation',
        'FeatureOfInterest': station, 'ObservedProperty': PARAMETER,
    }, quote_via=quote)
    return {
        'community': community, 'source_name': 'Greater Wellington', 'source_url': SOURCE_URL,
        'station_name': station, 'station_url': station_url, 'lawa_url': LAWA_URL,
        'latitude': latitude, 'longitude': longitude,
        'station_latitude': latitude, 'station_longitude': longitude,
        'station_distance_km': _distance(COMMUNITY_POINTS[community], latitude, longitude),
        'samples': [],
        'notice': 'A laboratory sample describes one station at its sampling time, not current conditions across the bay. Check LAWA for current swimming advice.',
        'attribution': 'Monitoring data: Greater Wellington Regional Council. Retrieved directly from its public Hilltop service; LAWA swimming guidance is linked separately.',
        'documentation_url': ENDPOINT,
    }


def parse(data, community, now=None):
    """Validate WaterML2 identity, measurement, units and sampling timestamps."""
    result = metadata(community)
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError('The council water-data response is too large or invalid.')
    # Reject DTDs/entities before parsing. Only ordinary XML is needed here.
    if b'\x00' in data or re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', data, re.I):
        raise ValueError('The council water-data response contains unsupported XML declarations.')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError('The council water-data response could not be read.') from error
    if root.tag != '{http://www.opengis.net/waterml/2.0}Collection':
        raise ValueError('The council did not return a water measurement collection.')
    current = now or datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError('A timezone-aware retrieval time is required.')
    candidates = []
    for observation in root.findall('wml2:observationMember/om:OM_Observation', NS):
        site = observation.find('om:featureOfInterest', NS)
        parameter = observation.find('om:observedProperty', NS)
        if site is None or site.get(XLINK_TITLE) != result['station_name']:
            raise ValueError('The council returned measurements for a different station.')
        if parameter is None or parameter.get(XLINK_TITLE) != PARAMETER:
            raise ValueError('The council returned a different measurement type.')
        series = observation.find('om:result/wml2:MeasurementTimeseries', NS)
        if series is None:
            continue
        unit_element = series.find('wml2:defaultPointMetadata/wml2:DefaultTVPMeasurementMetadata/wml2:uom', NS)
        unit = unit_element.get('code', '').strip() if unit_element is not None else ''
        if unit.lower().replace(' ', '') not in {'n/100ml', 'cfu/100ml', 'mpn/100ml'}:
            raise ValueError('The council sample has missing or unexpected units.')
        for point in series.findall('wml2:point/wml2:MeasurementTVP', NS):
            timestamp = (point.findtext('wml2:time', '', NS) or '').strip()
            value = (point.findtext('wml2:value', '', NS) or '').strip()
            value_match = re.fullmatch(r'(<=|>=|<|>|≤|≥)?\s*(\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d+))?', value)
            if len(value) > 40 or not value_match:
                raise ValueError('The council sample has an invalid result value.')
            try:
                numeric = Decimal(value_match.group(2) + ('E' + value_match.group(3) if value_match.group(3) else ''))
                sampled = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            except (ValueError, InvalidOperation) as error:
                raise ValueError('The council sample has an invalid result or sampling time.') from error
            if not numeric.is_finite() or sampled.tzinfo is None or sampled > current:
                raise ValueError('The council sample has an invalid result or sampling time.')
            candidates.append((sampled, {'parameter': PARAMETER, 'value': value, 'value_text': value,
                                        'qualifier': value_match.group(1) or None,
                                        'unit': unit, 'sampled_at': sampled.isoformat()}))
    if not candidates:
        raise ValueError('No published Enterococci Bacteria sample is available for this station.')
    # A latest-value request should have one result. If more are supplied, retain
    # just the latest valid dated observation, never response generation time.
    result['samples'] = [max(candidates, key=lambda item: item[0])[1]]
    return result


def fetch(community, opener=urlopen, now=None):
    """One fixed public GET per community; the caller controls cache freshness."""
    details = metadata(community)
    request = Request(details['station_url'], headers={
        'Accept': 'application/xml, text/xml', 'User-Agent': 'WAINET/1.0 (public coastal monitoring)',
    })
    with opener(request, timeout=TIMEOUT_SECONDS) as response:
        # An unexpected redirect must never become an unverified data source.
        if callable(getattr(response, 'geturl', None)):
            destination = urlsplit(response.geturl())
            if destination.scheme != 'https' or destination.netloc != 'hilltop.gw.govt.nz' or destination.path != '/data.hts':
                raise ValueError('The council water-data service redirected to an unexpected address.')
        data = response.read(MAX_BYTES + 1)
    return parse(data, community, now)
