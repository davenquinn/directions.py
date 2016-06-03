"""
Classes for major routing providers
* Google
* Mapquest
* Mapquest Open
* Mapbox

Please consult the terms of service of each provider before using the service
Google - https://developers.google.com/maps/terms
Mapquest - Contact for licensed data agreement
Mapquest Open - http://developer.mapquest.com/web/info/terms-of-use
Mapbox - https://www.mapbox.com/tos/

"""
import itertools
import json

import polycomp
import requests

from .base import Router, Route, Maneuver, Waypoint


class Google(Router):
    url = 'http://maps.googleapis.com/maps/api/directions/json'
    default_name = 'google'
    modes = ('driving','cycling','walking','transit')

    def __init__(self, *args, **kwargs):
        Router.__init__(self, *args, **kwargs)

    # https://developers.google.com/maps/documentation/directions/
    def _convert_coordinate(self, p, t=Waypoint.VIA):
        if isinstance(p, str):
            return p
        if t == Waypoint.VIA:
            via = 'via:'
        else:
            via = ''

        # Google wants lat / lon
        return '{via}{0[1]:.6f},{0[0]:.6f}'.format(p, via=via)

    def _query_params(self, waypoints):
        origin = waypoints[0]
        destination = waypoints[-1]
        vias = waypoints[1:-1]
        # This assumes you're not running Python on a device with a location
        # sensor.
        payload = {
            'origin': self._convert_coordinate(origin, t=None),
            'destination': self._convert_coordinate(destination, t=None),
            'sensor': 'false',
            'units': 'metric',
        }
        if vias:
            payload['waypoints'] = '|'.join(self._convert_coordinate(v)
                                            for v in vias)
        return payload

    def raw_query(self, waypoints, **kwargs):


        payload = self._query_params(waypoints)

        # Set transit mode of request
        mode = kwargs.pop('mode',None)
        if mode == 'cycling':
            # Name change
            mode = 'bicycling'
        if mode is not None:
            payload['mode'] = mode

        payload.update(kwargs)

        r = requests.get(self.url, params=payload)
        r.raise_for_status()

        return r.json()

    def format_output(self, data):
        routes = []
        for r in data['routes']:
            duration = sum(leg['duration']['value'] for leg in r['legs'])
            distance = sum(leg['distance']['value'] for leg in r['legs'])

            maneuvers = []
            latlons = []
            # Legs are the spans of the route between waypoints desired. If
            # there are no waypoints, there will only be 1 leg
            for leg in r['legs']:
                for step in leg['steps']:
                    loc = step['start_location']
                    m = Maneuver((loc['lng'], loc['lat']),
                                 text=step['html_instructions'])
                    maneuvers.append(m)
                    d = step['polyline']['points'].encode('utf-8')
                    latlons.append(
                        polycomp.decompress(d))

            # latlons is a list of list of lat/lon coordinate pairs. The end
            # point of each list is the same as the first point of the next
            # list. Get rid of the duplicates
            lines = [x[:-1] for x in latlons]
            lines.append([latlons[-1][-1]])  # Add the very last point
            points = itertools.chain(*lines)

            # Reverse lat/lon to be lon/lat for GeoJSON
            coords = [tuple(reversed(c)) for c in points]

            route = Route(coords, distance, duration, maneuvers=maneuvers)
            routes.append(route)

        return routes


class Mapquest(Router):
    # http://www.mapquestapi.com/directions/
    url = 'http://www.mapquestapi.com/directions/v2/route'
    default_name = 'mapquest'

    def __init__(self, key, *args, **kwargs):
        Router.__init__(self, *args, **kwargs)
        self.key = key

    def _convert_location(self, location, t=Waypoint.VIA):
        if t == Waypoint.VIA:
            via = 'v'
        else:
            via = 's'
        if isinstance(location, str):
            return {'street': location, 'type': via}
        else:
            return {'latLng': {'lat': location[1], 'lng': location[0]},
                    'type': via}

    def _format_waypoints(self, waypoints):
        # Mapquest takes in locations as an array
        locations = [self._convert_location(waypoints[0], t=Waypoint.STOP)]
        if waypoints:
            locations.extend(self._convert_location(loc, t=Waypoint.VIA)
                             for loc in waypoints[1:-1])
        locations.append(self._convert_location(waypoints[-1],
                                                t=Waypoint.STOP))

        return locations

    def raw_query(self, waypoints, **kwargs):
        params = {
            'key': self.key,
            'inFormat': 'json',
            'outFormat': 'json',
        }

        locations = self._format_waypoints(waypoints)
        data = {
            'locations': locations,
            'options': {
                'avoidTimedConditions': False,
                'shapeFormat': 'cmp',
                'generalize': 0,  # No simplification
                'unit': 'k',
            },
        }
        data = json.dumps(data, separators=(',', ':'))

        r = requests.post(self.url,
                          params=params,
                          data=data)
        r.raise_for_status()
        data = r.json()
        status_code = data['info']['statuscode']
        if status_code != 0:
            raise Exception(data['info']['messages'][0])

        return data

    def format_output(self, data):
        latlons = polycomp.decompress(data['route']['shape']['shapePoints'])
        coords = [tuple(reversed(c)) for c in latlons]
        duration = data['route']['time']
        distance = data['route']['distance'] * 1000  # km to m

        maneuvers = []
        for leg in data['route']['legs']:
            for m_in in leg['maneuvers']:
                loc = m_in['startPoint']
                m = Maneuver((loc['lng'], loc['lat']),
                             text=m_in['narrative'])
                maneuvers.append(m)
        r = Route(coords, distance, duration, maneuvers=maneuvers)

        return [r]


class MapquestOpen(Mapquest):
    # http://open.mapquestapi.com/directions/
    # This is the same interface as Mapquest (for now) but just hits
    # a different url
    url = 'http://open.mapquestapi.com/directions/v2/route'
    default_name = 'mapquestopen'


class Mapbox(Router):
    default_name = 'mapbox'
    modes = ('driving','cycling','walking')

    # https://www.mapbox.com/developers/api/directions/
    def __init__(self, access_token, *args, **kwargs):
        Router.__init__(self, *args, **kwargs)
        self.access_token = access_token

    def _convert_coordinate(self, p):
        return '{0[0]},{0[1]}'.format(p)

    def raw_query(self, waypoints, **kwargs):
        baseurl = 'https://api.mapbox.com/v4/directions/{profile}/{waypoints}.json?access_token={access_token}'
        formatted_points = ';'.join(self._convert_coordinate(p)
                                    for p in waypoints)

        mode = kwargs.pop('mode',self.modes[0])

        url = baseurl.format(
                access_token=self.access_token,
                profile='mapbox.'+mode,
                waypoints=formatted_points)
        payload = {'alternatives': 'false'}
        r = requests.get(url, params=payload)

        r.raise_for_status()
        return r.json()

    def format_output(self, data):
        routes = []
        for r in data['routes']:
            maneuvers = [Maneuver(s['maneuver']['location']['coordinates'],
                                  text=s['maneuver']['instruction'])
                         for s in r['steps']]
            route = Route(r['geometry']['coordinates'],
                          r['distance'],
                          r['duration'], maneuvers=maneuvers)
            routes.append(route)
        return routes
