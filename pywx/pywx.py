#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pythabot
import forecastio
import functools
import logging
import collections
import datetime
import time
import requests
import simplejson
import re
import pytz
import dataset
import csv

db = dataset.connect('sqlite:///pywx.db')
usertable = db['users']

#load airports
airportcsv = csv.reader(open('airports.dat'))
airport = collections.namedtuple('Airport', 'airport_id name city country faa icao lat long alt tz dst')
airport_lookup = {}
for ap in airportcsv:
    ap = map(lambda x: '' if x == '\\N' else x, ap)
    apo = airport(*ap)
    if apo.faa and apo.faa != '\\N':
        airport_lookup[apo.faa.lower()] = apo
    if apo.icao and apo.icao != '\\N':
        airport_lookup[apo.icao.lower()] = apo

from geopy.geocoders import GoogleV3
geoloc = GoogleV3()

fio_api_key = "0971c933da4dcd6e3fe4f01ccf62a90a"
max_msg_len = 375

config = {
    "host": "irc.slashnet.org",
    "port": 6667,
    "nick": "wx",
    "ident": "wx",
    "realname": "wx",
    "pass": "",
    "chans": ["#mefi"],
    "admins": ["~mach5@cloak-FBE60E9A.hsd1.nj.comcast.net"],
    "ownermask": "~mach5@cloak-FBE60E9A.hsd1.nj.comcast.net",
    "quitmsg": "peace out"
}

def quit(parseinfo):
    bot.quit("Quit")

def catch_failure(func):
    @functools.wraps(func)
    def wrapper(parseinfo):
        try:
            func(parseinfo)
        except Exception, e:
            logging.exception("bot error")
            bot.privmsg('mach5', str(e))
    return wrapper

def smart_print_return(func):
    @functools.wraps(func)
    def wrapper(parseinfo):
        payload = func(parseinfo)
        msg = []
        for word in ' '.join(payload).split(' '):
            msg.append(word)
            if sum(map(len, msg)) > max_msg_len:
                bot.privmsg(parseinfo['chan'], " ".join(msg[:-1]))
                msg = [word]
        bot.privmsg(parseinfo['chan'], " ".join(msg))
    return wrapper


latlong_re = re.compile(r'([0-9.-]+),([0-9.-]+)')

def match_location(username, args):
    args = " ".join(args)
    name = ""
    lat, lng = 0,0
    match = False

    #db lookup
    if not args:
        user = usertable.find_one(user=username)
        if user and user['place'] and user['latitude'] and user['longitude']:
            return user['place'], user['latitude'], user['longitude']

    #latlong
    llmatch = latlong_re.match(args.lower())
    if llmatch:
        name = "%s,%s" % (lat, lng)
        lat, lng = llmatch.groups()
        match = True

    airport = airport_lookup.get(args)
    if not match and airport:
        match = True
        code = "(%s)" % ('/'.join(filter(lambda x: bool(x), [airport.faa, airport.icao])))
        if airport.name == airport.city:
            name = "%s, %s %s" % (airport.city, airport.country, code)
        else:
            name = "%s, %s, %s %s" % (airport.name, airport.city, airport.country, code)
        lat = airport.lat
        lng = airport.long

    if not match:
        try:
            loc = geoloc.geocode(args)
            name = loc.address
            lat = loc.latitude
            lng = loc.longitude
        except:
            return None, None, None

    usertable.upsert(dict(user=username, place=name, latitude=lat, longitude=lng), ['user'])
    return name, lat, lng

cmap = {'black': '\x031','navy': '\x032','maroon': '\x035','green': '\x033','grey': '\x0314','royal': '\x0312','aqua': '\x0311',
        'lime': '\x039','silver': '\x0315','orange': '\x037','pink': '\x0313','purple': '\x036','red': '\x034','teal': '\x0310',
        'white': '\x030','yellow': '\x038','null': '\x03'}
icon_colors = {
    'clear-day': 'white',
    'clear-night': 'white',
    'rain': 'aqua',
    'snow': 'purple',
    'sleet': 'pink',
    'wind': 'royal',
    'fog': 'silver',
    'cloudy': 'silver',
    'partly-cloudy-day': 'silver',
    'partly-cloudy-night': 'silver',
    'hail': 'pink',
    'thunderstorm': 'red',
    'tornado': 'red'
}
cc = lambda s,c: "%s%s%s" % (cmap[c], s, cmap['null']) if c != 'null' else s
pht = lambda t,u='F',c='royal': cc("⇑ %s°%s".decode('utf-8') % (int(t), u), c)
plt = lambda t,u='F',c='navy': cc("⇓ %s°%s".decode('utf-8') % (int(t), u), c)
pt = lambda t,u='F',c='null': "%s %s°%s%s".decode('utf-8') % (cmap[c], int(t), u, cmap['null']) if c != 'null' else " %s°%s".decode('utf-8') % (int(t), u)
ncc = lambda s: cc(s, 'orange')
tcc = lambda s: cc(s, 'royal')
icc = lambda s,i: cc(s, icon_colors[i])

def color_strip(s):
    for code in sorted(cmap.values(), key=lambda x: len(x), reverse=True):
        s = re.sub(code, '', s)
    return s

unitobj = collections.namedtuple("UnitSet", 'wind, dist, temp, intensity, accum, press')
def get_units(unitset):
    if unitset == 'us':
        return unitobj('mph', 'mi', 'F', 'in/hr', 'in', 'mbar')
    if unitset == 'si':
        return unitobj('m/s', 'km', 'C', 'mm/hr', 'cm', 'hPa')
    if unitset == 'ca':
        return unitobj('kph', 'km', 'C', 'mm/hr', 'cm', 'hPa')
    if unitset == 'uk':
        return unitobj('mph', 'km', 'C', 'mm/hr', 'cm', 'hPa')
    logging.error('unknown units: %s' % unitset)
    return unitobj('m/s', 'km', 'C', 'mm/hr', 'cm', 'hPa')


epoch_dt = lambda ts: datetime.datetime.fromtimestamp(ts)
epoch_tz_dt = lambda ts, tz='UTC': datetime.datetime.fromtimestamp(ts, tz=pytz.utc).astimezone(pytz.timezone(tz))
to_celcius = lambda f: (f-32)*5/9
to_fahrenheight = lambda c: (c*9/5)+32
wind_chill = lambda t, ws: int(35.74 + (0.6215*t) - 35.75*(ws**0.16) + 0.4275*t*(ws**0.16))
wind_chill_si = lambda t, ws: int(13.12 + (0.6215*t) - 11.37*(ws**0.16) + 0.3965*t*(ws**0.16))
wind_directions = [(11.25, 'N'),(33.75, 'NNE'),(56.25, 'NE'),(78.75, 'ENE'),(101.25, 'E'),(123.75, 'ESE'),
                   (146.25, 'SE'),(168.75, 'SSE'),(191.25, 'S'),(213.75, 'SSW'),(236.25, 'SW'),(258.75, 'WSW'),
                   (281.25, 'W'),(303.75, 'WNW'),(326.25, 'NW'),(348.75, 'NNW'),(360, 'N')]
first_greater_selector = lambda i, l: [r for c, r in l if c >= i][0]
wind_direction = lambda bearing: first_greater_selector(bearing, wind_directions)
mag_words = [(5,'light'),(6,'moderate'),(7,'STRONG'),(8,'MAJOR'),(9,'GREAT'),(10,'CATASTROPHIC'),]
mag_colors = [(5,'yellow'),(6,'orange'),(7,'red'),(8,'red'),(9,'red'),(10,'red'),]
mag_word = lambda mag: first_greater_selector(mag, mag_words)
mag_color = lambda mag: first_greater_selector(mag, mag_colors)


def debug(parseinfo):
    import ipdb
    ipdb.set_trace()

def colors(parseinfo):
    bot.privmsg(parseinfo['chan'], " - ".join(["%s%s\x030" % (v,k) for k,v in cmap.iteritems()]))

def wf(parseinfo):
    args = parseinfo['args'][1:]
    name, lat, lng = match_location(parseinfo['sender'], args)
    if not name and not lat and not lng:
        return ['No location matches found for: %s' % ' '.join(args),]
    forecast = forecastio.load_forecast(fio_api_key, float(lat), float(lng))
    units = get_units(forecast.json['flags']['units'])

    payload = ['%s:' % ncc(name)]
    for d in forecast.daily().data[:5]:
        payload.append("%s: %s (%s/%s)".decode('utf-8') % (
            cc(d.time.strftime('%a'), 'maroon'),
            icc(d.summary, d.icon),
            pht(d.temperatureMax, units.temp),
            plt(d.temperatureMin, units.temp)
        ))
    return payload

@catch_failure
@smart_print_return
def nwf(parseinfo):
    return map(color_strip, wf(parseinfo))

@catch_failure
@smart_print_return
def cwf(parseinfo):
    return wf(parseinfo)

def wx(parseinfo):
    args = parseinfo['args'][1:]
    name, lat, lng = match_location(parseinfo['sender'], args)
    if not name and not lat and not lng:
        return ['No location matches found for: %s' % ' '.join(args),]
    forecast = forecastio.load_forecast(fio_api_key, float(lat), float(lng))
    units = get_units(forecast.json['flags']['units'])
    current = forecast.currently()

    payload = []
    payload.append('%s: %s%s' % (ncc(name), icc(current.summary, current.icon), pt(current.temperature, units.temp)))
    if current.temperature < 50 and current.windspeed > 3 and units.temp == "F":
        wc = wind_chill(current.temperature, current.windspeed)
        payload.append('%s%s' % (tcc('Wind Chill:'), pt(wc, units.temp)))
    if current.temperature < 10 and current.windspeed > 5 and units.temp == "C":
        wc = wind_chill_si(current.temperature, current.windspeed)
        payload.append('%s%s' % (tcc('Wind Chill:'), pt(wc, units.temp)))

    payload.append('%s %s%s from %s' % (tcc('Winds:'), int(current.windspeed), units.wind, wind_direction(current.windbaring)))
    payload.append('%s %s%%' % (tcc('Clouds:'), int(current.cloudcover)*100))
    payload.append('%s%s' % (tcc('Dewpoint:'), pt(current.dewPoint, units.temp)))
    payload.append('%s %s%%' % (tcc('Humidity:'), int(current.humidity*100)))
    payload.append('%s %s %s' % (tcc('Pressure:'), int(current.pressure), units.press))

    today = forecast.daily().data[0]
    if today.sunriseTime and today.sunsetTime:
        delta = today.sunsetTime - today.sunriseTime
        daytime = '%sh%sm' % (delta.seconds/60/60, delta.seconds/60%60)
        payload.append('%s ⇑ %s ⇓ %s %s'.decode('utf-8') % (tcc('Sun:'), today.sunriseTime.strftime('%I:%M%p').lower(),
                                                          today.sunsetTime.strftime('%I:%M%p').lower(), daytime))

    alerts = forecast.json['alerts'] if 'alerts' in forecast.json else None
    if alerts:
        payload.append('%s' % cc('Alerts:', 'red'))
        payload.append(", ".join(['#%s: %s' % (c+1, cc(a['title'], 'orange')) for c, a in enumerate(alerts)]))

    return payload

@catch_failure
@smart_print_return
def nwx(parseinfo):
    return map(color_strip, wx(parseinfo))

@catch_failure
@smart_print_return
def cwx(parseinfo):
    return wx(parseinfo)

@catch_failure
@smart_print_return
def alerts(parseinfo):
    args = parseinfo['args'][1:]
    name, lat, lng = match_location(parseinfo['sender'], args)
    forecast = forecastio.load_forecast(fio_api_key, float(lat), float(lng))

    payload = []
    payload.append('%s: ' % (ncc(name)))

    #alerts
    alerts = forecast.json['alerts'] if 'alerts' in forecast.json else None
    if alerts:
        payload.append('%s' % cc('Alerts:', 'red'))
        payload.append(", ".join(['#%s: %s' % (c+1, cc(a['title'], 'orange')) for c, a in enumerate(alerts)]))

    payload.append("Use 'alert # <location>' to retrieve alert text")

    return payload

@catch_failure
def alert(parseinfo):
    args = parseinfo['args'][2:]
    alert_index = parseinfo['args'][1]
    if '#' in alert_index:
        alert_index = alert_index.lstrip('#')
    alert_index = int(alert_index)

    name, lat, lng = match_location(parseinfo['sender'], args)
    forecast = forecastio.load_forecast(fio_api_key, float(lat), float(lng))

    alerts = forecast.json['alerts'] if 'alerts' in forecast.json else None
    if alerts:
        alert = alerts[alert_index-1]
        bot.privmsg(parseinfo['sender'], alert['title'])
        bot.privmsg(parseinfo['sender'], alert['uri'])
        for line in alert['description'].split('\n'):
            bot.privmsg(parseinfo['sender'], str(line))
        bot.privmsg(parseinfo['sender'], epoch_dt(alert['expires']).strftime('Expires: %Y-%m-%d %H:%M'))

@catch_failure
@smart_print_return
def buttcoin(parseinfo):
    resp = requests.get('http://api.bitcoincharts.com/v1/markets.json')
    markets = resp.json()

    mdict = {}
    for market in markets:
        mdict[market['symbol']] = market

    symbol = "btceUSD"
    if len(parseinfo['args']) > 1:
        symbol = parseinfo['args'][1]
    market = mdict.get(symbol, 'btceUSD')
    inverse = round(1.0/market['close'], 5)
    last_trade = epoch_dt(market['latest_trade'])
    ago = (datetime.datetime.now()-last_trade).seconds
    payload = ["%s (%s): Last: $%s ($1 = %s)" % (market['symbol'], market['currency'], market['close'], inverse),]
    payload.append("/ High: $%s / Low: $%s / Volume: %s" % (market['high'], market['low'], int(market['volume'])))
    payload.append("/ Bid: $%s / Ask: $%s / Last Trade: %s (%ss ago)" % (market['bid'], market['ask'],
                                                                         last_trade.strftime("%Y-%m-%d %H:%M:%S EST"), ago))
    return payload

global eqdb
eqdb = None

@catch_failure
def earthquake_monitor(parseinfo):
    global eqdb
    resp = requests.get('http://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_hour.geojson')
    earthquakes = resp.json()['features']
    if eqdb is None:
        eqdb = []
        for eq in earthquakes:
            eqdb.append(eq['properties']['code'])

    for eq in earthquakes:
        if eq['properties']['code'] in eqdb:
            continue
        else:
            eqdb.append(eq['properties']['code'])
        printquake(parseinfo['chan'], eq)

@catch_failure
def lastquakes(parseinfo):
    resp = requests.get('http://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson')
    earthquakes = resp.json()['features']

    for eq in earthquakes[:5]:
        printquake(parseinfo['chan'], eq)

@catch_failure
def lastbigquakes(parseinfo):
    resp = requests.get('http://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.geojson')
    earthquakes = resp.json()['features']

    for eq in earthquakes:
        printquake(parseinfo['chan'], eq)

def printquake(chan, eq):
    eqp = eq['properties']
    magnitude = eqp.get('mag')
    if not magnitude:
        return
    descriptor = mag_word(float(magnitude))
    color = mag_color(float(magnitude))
    lat, lng, depth = eq['geometry']['coordinates']
    region = eqp['place']
    url = eqp['url']

    localtime = datetime.datetime.fromtimestamp(eqp['time']/1000, tz=pytz.utc) + datetime.timedelta(minutes=eqp.get('tz', 0))
    localtime = localtime.strftime('%m/%d %I:%M:%p')

    timedelta = datetime.datetime.now() - epoch_dt(eqp['time']/1000)
    h, m, s = timedelta.seconds/60/60, timedelta.seconds/60%60, timedelta.seconds%60%60
    ago = '%sh%sm%ss' % (h,m,s) if h and m and s else '%sm%ss' % (m,s) if m and s else '%ss' % (s)

    quake = []
    quake.append("A %s earthquake has occured. Magnitude: %s" % (cc(descriptor, color), cc("◼ %s".decode('utf-8') % magnitude, color)))
    quake.append("Depth: %s km Region: %s Local Time: %s (%s ago)" % (depth, region, localtime, ago))
    if eqp['tsunami'] and int(eqp['tsunami']) == 1:
        quake.append(cc('A tsunami may have been generated.', 'red'))
    quake.append("%s" % url)

    bot.privmsg(chan, ' '.join(quake))

@catch_failure
@smart_print_return
def housewx(parseinfo):
    if parseinfo['sender'] == 'dmd':
        json = requests.get('http://3e.org/nest/').json()
        payload = ["House is at%s" % pt(json['current_state']['temperature']),]
        payload.append("with the system in mode %s and" % (json['current_state']['mode']))
        payload.append("set to%s. Time to target is %s." % (pt(json['target']['temperature']), json['target']['time_to_target']))
        payload.append("Auto away is %s." % (json['current_state']['auto_away']))
        payload.append("Manual away is %s." % (json['current_state']['manual_away']))
        return payload

    auth = {
        'UserName': '',
        'Password': '',
        'RememberMe': 'true',
        'timeOffset': 240
    }
    headers = {'X-Requested-With': 'XMLHttpRequest'}
    authreq = requests.post('https://rs.alarmnet.com/TotalConnectComfort/', data=auth)
    wxreq = requests.get('https://rs.alarmnet.com/TotalConnectComfort/Device/CheckDataSession/398466',
                         cookies=authreq.history[0].cookies,
                         headers=headers)
    json = wxreq.json()
    data = json['latestData']['uiData']
    switch = data['SystemSwitchPosition']
    curtemp = data['DispTemperature']

    switch_name = {0: 'EMERGENCY HEATING', 1: 'heating', 2: 'off', 3: 'cooling', 4: 'autoheating',
                     5: 'autocooling', 6: 'southern away?', 7: 'unknown'}[switch]
    setpoint_status = {0: 'on schedule', 1: 'temporarily holding', 2: 'holding', 3: 'in vacation mode'}
    fan_modes = {0: 'on auto', 1: 'on', 2: 'circulating', 3: 'following schedule', 4: 'unknown'}

    if switch_name == "off":
        status = "with the system off."
    if switch_name == "heating":
        setpoint = data['HeatSetpoint']
        status = "with the system heating and set to%s." % pt(setpoint)
    if switch_name == "cooling":
        setpoint = data['CoolSetpoint']
        status = "with the system cooling and set to%s." % pt(setpoint)

    fan_status = "Fan is %s." % fan_modes[json['latestData']['fanData']['fanMode']]

    payload = ["House is at%s," % pt(curtemp), status, fan_status]
    return payload


if __name__ == '__main__':
    bot = pythabot.Pythabot(config)

    bot.addCommand("botquit",quit,"owner")
    bot.addCommand("wf", cwf, "all")
    bot.addCommand("wx", cwx, "all")
    bot.addCommand("nwf", nwf, "all")
    bot.addCommand("nwx", nwx, "all")
    bot.addCommand("housewx", housewx, "all")
    bot.addCommand("buttcoin", buttcoin, "all")
    bot.addCommand("alerts", alerts, "all")
    bot.addCommand("alert", alert, "all")
    bot.addCommand("ipdb", debug, "owner")
    bot.addCommand("colors", colors, "all")
    bot.addCommand("lastquakes", lastquakes, "all")
    bot.addCommand("lastbigquakes", lastbigquakes, "all")
    bot.addPeriodicCommand(earthquake_monitor)
    bot.connect()
    bot.listen()
