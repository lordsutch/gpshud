#!/usr/bin/env python3
#
# by
# Pavan Pamidimarri

# from __future__ import absolute_import, print_function, division

import gi
gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GLib, GObject, Gtk

import argparse
import collections
import os
import pathlib
import time
import datetime
import statistics
from socket import error as SocketError

import dateutil.parser
import gps
import gps.clienthelpers
import astral
import astral.sun

# Need to adapt to new calling conventions
# from astral import Astral, Location

FONTS = ('Roboto Slab', 'Inter', 'Roboto', 'Piboto', 'Open Sans',
         'DejaVu Sans')

GNSS_MAP = {
    0: 'GPS',
    1: 'SBAS',
    2: 'Galileo',
    3: 'Beidou',
    4: 'IMES',
    5: 'QZSS',
    6: 'GLONASS',
}

GNSS_FLAG_ISO = {
    0: 'US',
    2: 'EU',
    3: 'CN',
    4: 'IN',
    5: 'JP',
    6: 'RU',
}

GNSS_FLAG = {k: ''.join(chr(0x1f1e6+ord(x)-ord('A')) for x in v)
             for k, v in GNSS_FLAG_ISO.items()}

GNSS_FLAG[1] = '\N{SATELLITE}'  # SBAS


def format_latitude(latitude: float) -> str:
    hemisphere = 'N' if latitude >= 0 else 'S'
    return f'{abs(latitude):.5f}°\u200a{hemisphere}'


def format_longitude(longitude: float) -> str:
    hemisphere = 'E' if longitude >= 0 else 'W'
    return f'{abs(longitude):.5f}°\u200a{hemisphere}'


class Handler:
    def onDestroy(self, *args):
        Gtk.main_quit()


class HeadUpDisplay(Gtk.Window):
    def __init__(self, speed_unit=None, altitude_unit=None):
        GObject.GObject.__init__(self)
        self.MPH_UNIT_LABEL = 'mph'
        self.KPH_UNIT_LABEL = 'km/h'
        self.KNOTS_UNIT_LABEL = 'knots'
        self.conversions = {
                self.MPH_UNIT_LABEL: gps.MPS_TO_MPH,
                self.KPH_UNIT_LABEL: gps.MPS_TO_KPH,
                self.KNOTS_UNIT_LABEL: gps.MPS_TO_KNOTS
        }
        self.speed_unit = speed_unit or self.MPH_UNIT_LABEL
        if self.speed_unit not in self.conversions:
            raise TypeError(
                    '%s is not a valid speed unit'
                    % (repr(speed_unit))
            )
        self.speedfactor = self.conversions[self.speed_unit]
        self.METER_UNIT_LABEL = 'm'
        self.FOOT_UNIT_LABEL = 'ft'
        self.alt_conversions = {
                self.METER_UNIT_LABEL: 1,
                self.FOOT_UNIT_LABEL: gps.METERS_TO_FEET,
        }
        self.altitude_unit = altitude_unit or 'ft'
        if self.altitude_unit not in self.alt_conversions:
            raise TypeError(
                    '%s is not a valid altitude unit'
                    % (repr(altitude_unit))
            )
        self.altfactor = self.alt_conversions[self.altitude_unit]
        self.last_speed = 0
        self.last_heading = 0
        self.last_mode = 0
        self.last_status = 0
        self.latitude = None
        self.longitude = None
        self.altitude = None
        self.skyview = None
        self.last_tpv = None

        self.font_face = ''
        context = self.create_pango_context()
        families = (fam.get_name() for fam in context.list_families())
        for font in FONTS:
            if font in families:
                self.font_face = font
                break

        self.now_fmt = '%-I:%M %p'
        self.date_fmt = '%a, %b %-d'
        self.heading_markup = "<span font='28' face='" + self.font_face + "' color='%s'>%s</span>"
        self.speed_markup = "<span font='140' face='" + self.font_face + "' color='%s' font_features='tnum=1,lnum=1'>%s</span>"
        self.unit_markup = "<span font='14' face='" + self.font_face + "' color='%s'><b>%s</b></span>"
        self.today_markup = "<span font='17.5' face='" + self.font_face + "' color='%s' font_features='tnum=1,lnum=1'>%s</span>"
        self.now_markup = "<span font='21' face='" + self.font_face + "' weight='bold' color='%s' font_features='tnum=1,lnum=1'>%s</span>"
        self.fix_markup = f"<span font='17.5' face='{self.font_face}' color='%s' font_features='tnum=1,lnum=1'>%s</span>"
        self.position_markup = f"<span font='17.5' face='{self.font_face}' color='%s' font_features='tnum=1,lnum=1'>%s</span>"
        self.thick_blank_markup = "<span font='12' color='#000000'> </span>"
        self.thin_blank_markup = "<span font='2' color='#000000'> </span>"

        self.builder = Gtk.Builder()
        filename = str(pathlib.Path(__file__).parent / "gpshud.glade")
        self.builder.add_from_file(filename)
        self.builder.connect_signals(Handler())
        self.builder.get_object("window1").override_background_color(
            Gtk.StateType.NORMAL, Gdk.RGBA(0, 0, 0, 1))

        # self.builder.get_object("TopBlank").set_markup(self.thin_blank_markup)
        # self.builder.get_object("Blank1").set_markup(self.thin_blank_markup)
        # self.builder.get_object("Blank2").set_markup(self.thick_blank_markup)
        # self.builder.get_object("Blank3").set_markup(self.thick_blank_markup)
        # self.builder.get_object("Blank4").set_markup(self.thick_blank_markup)
        # self.builder.get_object("Blank5").set_markup(self.thick_blank_markup)
        # self.builder.get_object("BottomBlank").set_markup(self.thin_blank_markup)
        self.update_data()

    def update_data(self):
        if self.is_day():
            color = '#FFFFFF'
            unitcolor = '#888888'
        else:
            color = '#BBBBBB'
            unitcolor = '#666666'

        # if self.last_mode in (0, 1):
        #     color = '#000000'
        #     unitcolor = '#000000'

        self.builder.get_object("Heading").set_markup(self.heading_markup % (
                color, self.get_direction_text(self.last_heading)))
        self.builder.get_object("Speed").set_markup(self.speed_markup % (
                color, self.get_speed_text(self.last_speed)))
        self.builder.get_object("Unit").set_markup(self.unit_markup % (
                unitcolor, self.speed_unit.upper()))

        if self.last_mode >= 2 and self.last_tpv.time:
            now = dateutil.parser.isoparse(self.last_tpv.time).astimezone()
            # print(now)
        else:
            now = datetime.datetime.now()

        dtstr = (self.today_markup % (color, now.strftime(self.date_fmt)) +
                 '\n' + self.now_markup % (color, now.strftime(self.now_fmt)))

        self.builder.get_object("Date").set_markup(dtstr)
        self.builder.get_object("Time").set_visible(False)
        # self.builder.get_object("Date").set_markup(self.today_markup % (
        #         color, now.strftime(self.date_fmt)))
        # self.builder.get_object("Time").set_markup(self.now_markup % (
        #         color, now.strftime(self.now_fmt)))

        if self.last_status:
            fixtext = ('Unknown', 'Normal', 'DGPS', 'RTK Fixed',
                       'RTK Floating', 'DR', 'GNSS+DR', 'Time (surveyed)',
                       'Simulated', 'P(Y)')[self.last_status]
        else:
            fixtext = ''

        if self.last_mode in (2, 3):
            fixtext += f' {self.last_mode}D fix'
        elif self.last_mode == 1:
            fixtext = 'No fix'
        else:
            fixtext = 'Unknown fix'

        if self.skyview and 'uSat' in self.skyview and 'nSat' in self.skyview:
            fixtext += f', {self.skyview.uSat}/{self.skyview.nSat} SVs'
            # gnss_info = collections.defaultdict(list)
            ucount = collections.defaultdict(int)
            ncount = collections.defaultdict(int)
            sigstrength = collections.defaultdict(float)
            usedsigstrength = collections.defaultdict(float)
            for sat in self.skyview.satellites:
                if 'gnssid' in sat:
                    # gnss_info[sat.gnssid].append(sat)
                    ucount[sat.gnssid] += int(sat.used)
                    ncount[sat.gnssid] += 1
                    if 'ss' in sat:
                        sigstrength[sat.PRN] = sat.ss
                        if sat.used:
                            usedsigstrength[sat.PRN] = sat.ss

            # svlist = (f'{GNSS_MAP[gnss][:2]}: {ucount[gnss]}/{ncount[gnss]}' for gnss in ncount if ucount[gnss])
            # svlist = (f'{GNSS_MAP[gnss][:2]}' for gnss in ncount if ucount[gnss])
            svlist = tuple(f'{ucount[gnss]} {GNSS_FLAG[gnss]}'
                           for gnss in ucount if ucount[gnss] > 0)
            if svlist:
                fixtext += '\n<span font="12">'+' '.join(svlist)+'</span>'
            if sigstrength:
                strengths = tuple(sigstrength.values())
                min_ss, max_ss = min(strengths), max(strengths)
                fixtext += f'\n<span font="10">All SNR: {min_ss:.0f}–{max_ss:.0f}'
                if len(strengths) > 1:
                    mean_ss = statistics.fmean(strengths)
                    sd_ss = statistics.stdev(strengths)
                    fixtext += f', \U0001D465\u0305={mean_ss:.1f}, \U0001D460={sd_ss:.1f}'
                fixtext += '</span>'
            if usedsigstrength:
                strengths = tuple(usedsigstrength.values())
                min_ss, max_ss = min(strengths), max(strengths)
                fixtext += f'\n<span font="10">Used SNR: {min_ss:.0f}–{max_ss:.0f}'
                if len(strengths) > 1:
                    mean_ss = statistics.fmean(strengths)
                    sd_ss = statistics.stdev(strengths)
                    fixtext += f', \U0001D465\u0305={mean_ss:.1f}, \U0001D460={sd_ss:.1f}'
                fixtext += '</span>'

        postext = ''
        if self.latitude is not None and self.longitude is not None:
            postext = (format_latitude(self.latitude) + '\n' +
                       format_longitude(self.longitude))
            if self.last_tpv and 'eph' in self.last_tpv:
                eph = self.last_tpv.eph*self.altfactor
                fixtext += f'\nCEP: ±\u200a{eph:.1f} {self.altitude_unit}'

            if self.altitude:
                alt = self.altitude * self.altfactor
                if self.last_tpv and 'epv' in self.last_tpv:
                    epv = self.last_tpv.epv*self.altfactor
                    postext += f'\n{alt:#.5n}\u200a±\u200a{epv:.1f} {self.altitude_unit}'
                else:
                    postext += f'\n{alt:#.5n} {self.altitude_unit}'

        self.builder.get_object("Fix").set_markup(self.fix_markup % (
                color, fixtext))

        self.builder.get_object('Position').set_markup(self.position_markup % (color, postext))
        return True

    def get_speed_text(self, speed):
        if self.last_mode in (0, 1):
            return "-"
        else:
            return '%.0f' % (speed * self.speedfactor)

    def get_direction_text(self, heading):
        if self.last_mode in (0, 1):
            direction = '-'
        else:
            direction = ('N', 'NE', 'E', 'SE',
                         'S', 'SW', 'W', 'NW', 'N')[int((heading+22.5)//45)]
        return direction

    def is_day(self):
        if self.longitude is None or self.latitude is None:
            return True

        solar_tz = datetime.timezone(datetime.timedelta(
            hours=(self.longitude+7.5) // 15))
        loc = astral.Observer(latitude=self.latitude, longitude=self.longitude)
        now = datetime.datetime.now(solar_tz)
        date = now.date()
        try:
            sunrise, sunset = astral.sun.daylight(loc, date, solar_tz)
            return sunrise <= now <= sunset
        except ValueError:
            noon = astral.sun.noon(loc, date=date)
            if astral.sun.elevation(noon) < 0:
                # Sun is not up at noon, so it's winter
                return False
            return True
        return False
    
        # l = Location()
        # l.latitude = self.latitude
        # l.longitude = self.longitude
        # current_time = datetime.now(l.tzinfo)
        # if (l.sunset() > current_time) and (l.sunrise() < current_time):
        #       return True
        # else:
        #       return False

class Main(object):
    def __init__(self, host='localhost', port=gps.GPSD_PORT, device=None,
                 debug=0, speed_unit=None, altitude_unit=None,
                 fullscreen=True):
        self.host = host
        self.port = port
        self.device = device
        self.debug = debug
        self.speed_unit = speed_unit
        self.altitude_unit = altitude_unit
        self.date_set = False
        self.daemon = None

        self.widget = HeadUpDisplay(speed_unit=self.speed_unit,
                                    altitude_unit=self.altitude_unit)
        self.window = self.widget.builder.get_object("window1")
        self.window.connect('delete_event', self.delete_event)
        self.window.connect('destroy', self.destroy)
        self.fullscreen = fullscreen
        self.window.show_all()
        if fullscreen:
            self.window.fullscreen()

    def watch(self, daemon: gps.gps, device):
        self.daemon = daemon
        self.device = device
        GLib.io_add_watch(daemon.sock, GLib.IO_IN, self.handle_response)
        GLib.io_add_watch(daemon.sock, GLib.IO_ERR, self.handle_hangup)
        GLib.io_add_watch(daemon.sock, GLib.IO_HUP, self.handle_hangup)
        return True

    def handle_response(self, source, condition):
        if not self.daemon:
            return False

        if self.daemon.read() == -1:
            self.handle_hangup(source, condition)
        if self.daemon.data['class'] == 'TPV':
            self.update_speed(self.daemon.data)
        elif self.daemon.data['class'] == 'SKY':
            self.update_sky(self.daemon.data)
        return True

    def handle_hangup(self, _dummy, _unused):
        w = Gtk.MessageDialog(
                parent=self.window,
                type=Gtk.MessageType.ERROR,
                flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
                buttons=Gtk.ButtonsType.OK
        )
        w.connect("destroy", lambda unused: Gtk.main_quit())
        w.set_title('GPSD Error')
        w.set_markup("GPSD has stopped sending data.")
        w.run()
        Gtk.main_quit()
        return True

    def update_sky(self, data):
        # print(data)
        self.widget.skyview = data
        self.widget.update_data()

    def update_speed(self, data):
        self.widget.last_tpv = data
        self.widget.last_mode = data.mode
        # if data.mode in (0, 1):
        #     self.renew_GPS()

        if not self.date_set:
            self.set_date()
        if 'status' in data:
            self.widget.last_status = data.status
        if 'speed' in data:
            self.widget.last_speed = data.speed
        if 'track' in data:
            self.widget.last_heading = data.track
        if 'lat' in data:
            self.widget.latitude = data.lat
        if 'lon' in data:
            self.widget.longitude = data.lon
        if 'altHAE' in data:
            self.widget.altitude = data.altHAE
        self.widget.update_data()

    def set_date(self):
        return
        # if self.daemon.utc != None and self.daemon.utc != '':
        #       gpsutc = self.daemon.utc[0:4] + self.daemon.utc[5:7] + self.daemon.utc[8:10] + ' ' + self.daemon.utc[11:19]
        #       ret_val = os.system('sudo date -u --set="%s"' % gpsutc)
        #       if ret_val == 0:
        #               self.date_set = True
        #       else:
        #               self.date_set = False
        # else:
        #       self.date_set = False

    def renew_GPS(self):
        if self.daemon:
            del self.daemon
            self.daemon = None

        try:
            daemon = gps.gps(
                    host=self.host,
                    port=self.port,
                    mode=gps.WATCH_ENABLE | gps.WATCH_JSON | gps.WATCH_SCALED,
                    verbose=self.debug
            )
            self.watch(daemon, self.device)
        except SocketError:
            w = Gtk.MessageDialog(
                    parent=self.window,
                    type=Gtk.MessageType.ERROR,
                    flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
                    buttons=Gtk.ButtonsType.OK
            )
            w.set_title('Socket Error')
            w.set_markup(
                    "Failed to connect to gpsd socket. Make sure that gpsd is running."
            )
            w.run()
            w.destroy()
        except KeyboardInterrupt:
            print("Keyboard interrupt")
            # self.window.emit('delete_event', Gdk.Event())
            del self.window
            #w.run()
            #w.destroy()

    def delete_event(self, _widget, _event, _data=None):
        return False

    def destroy(self, _unused, _empty=None):
        Gtk.main_quit()

    def run(self):
        try:
            daemon = gps.gps(
                    host=self.host,
                    port=self.port,
                    mode=gps.WATCH_ENABLE | gps.WATCH_JSON | gps.WATCH_SCALED,
                    verbose=self.debug
            )

            # cover = Gtk.Window()
            # cover.override_background_color(Gtk.StateType.NORMAL, Gdk.RGBA(0,0,0,1))
            # cover.fullscreen()
            # cover.show()

            for report in daemon:
                if report['class'] == 'TPV':
                    self.update_speed(report)
                    if report.mode >= 0:
                        # 2D/3D fix, good to go
                        break
                elif report['class'] == 'SKY':
                    self.update_sky(report)

            # cover.destroy()
            # del cover
            self.watch(daemon, self.device)
            Gtk.main()
        except SocketError:
            w = Gtk.MessageDialog(
                    parent=self.window,
                    type=Gtk.MessageType.ERROR,
                    flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
                    buttons=Gtk.ButtonsType.OK
            )
            w.set_title('Socket Error')
            w.set_markup(
                    "Failed to connect to gpsd socket. Make sure that gpsd is running."
            )
            w.run()
            w.destroy()
        except KeyboardInterrupt:
            print("Keyboard interrupt")
            del self.window
            #w.run()
            #w.destroy()

if __name__ == '__main__':
    default_units = gps.clienthelpers.unit_adjustments()
    # print(default_units)

    default_units_argument = ('imperial' if default_units.altunits == 'ft'
                              else 'metric')

    parser = argparse.ArgumentParser(description='Gtk+ HUD for GPSD')
    parser.add_argument(
        '--units', '-u', action='store', default=default_units_argument,
        choices=('metric', 'imperial', 'traditional', 'nautical'),
        help=f'units to use (default: {default_units_argument})')
    parser.add_argument('--fullscreen', action='store_true',
                        help='fit window to screen')
    parser.add_argument('--host', action='store', default='localhost',
                        help='GPSD host to connect to (default: localhost)')
    parser.add_argument(
        '--port', action='store', type=int, default=gps.GPSD_PORT,
        help=f'GPSD port to connect to (default: {gps.GPSD_PORT})')
    parser.add_argument('--debug', action='store', default=0, const=1,
                        type=int, metavar='LEVEL', nargs='?',
                        help='enable debugging output from gps module')
    args = parser.parse_args()

    if args.units in ('traditional', 'imperial'):
        speed_unit = 'mph'
        altitude_unit = 'ft'
    elif args.units == 'nautical':
        speed_unit = 'knots'
        altitude_unit = 'ft'
    elif args.units == 'metric':
        speed_unit = 'km/h'
        altitude_unit = 'm'
    else:
        speed_unit = default_units.speedunits
        altitude_unit = default_units.altunits

    Main(
        host=args.host,
        port=args.port,
        device=None,
        speed_unit=speed_unit,
        altitude_unit=altitude_unit,
        debug=args.debug,
        fullscreen=args.fullscreen,
    ).run()
