import io
import os
import functools
import yaml
import obspy
import seiscomp.core
import seiscomp.io
from .process import get_segment_window_duration, compute_me, SkipSegment

if obspy.UTCDateTime().__hash__() is None:
    obspy.UTCDateTime.__hash__ = lambda self: str(self).__hash__()

class WaveformCache(object):
    def __init__(self, rsUrl, cachesize):
        self.__rsUrl = rsUrl
        self.get = functools.lru_cache(cachesize)(self.__get)

    def __iter(self, rs):
        rec = rs.next()

        while rec:
            yield rec
            rec = rs.next()

    def __get(self, netCode, staCode, locCode, chaCode, starttime, endtime):
        rs = seiscomp.io.RecordStream.Open(self.__rsUrl)

        if not rs:
            raise Exception("Could not open %s" % self.__rsUrl)

        try:
            rs.addStream(netCode, staCode, locCode, chaCode,
                         seiscomp.core.Time.FromString(str(starttime), "%FT%T.%fZ"),
                         seiscomp.core.Time.FromString(str(endtime), "%FT%T.%fZ"))

            data = b"".join(rec.raw().bytes() for rec in self.__iter(rs))
            if not data:
                return None

            return obspy.read(io.BytesIO(data), format="MSEED")

        finally:
            rs.close()

class ExportSink(seiscomp.io.ExportSink):
    def __init__(self, fp):
        seiscomp.io.ExportSink.__init__(self)
        self.__fp = fp

    def write(self, data):
        self.__fp.write(data)
        return len(data)

class InventoryCache(object):
    def __init__(self, cachesize):
        self.get = functools.lru_cache(cachesize)(self.__get)

    def __get(self, netCode, staCode, locCode, chaCode):
        inv = seiscomp.client.Inventory.Instance().inventory()

        try:
            registrationEnabled = seiscomp.datamodel.PublicObject.IsRegistrationEnabled()
            seiscomp.datamodel.PublicObject.SetRegistrationEnabled(False)

            notifierEnabled = seiscomp.datamodel.Notifier.IsEnabled()
            seiscomp.datamodel.Notifier.SetEnabled(False)

            newInv = seiscomp.datamodel.Inventory()

            dataloggers = set()
            sensors = set()

            for i in range(inv.networkCount()):
                net = inv.network(i)
                if net.code() != netCode:
                    continue

                newNet = seiscomp.datamodel.Network(net)
                newInv.add(newNet)

                for j in range(net.stationCount()):
                    sta = net.station(j)
                    if sta.code() != staCode:
                        continue

                    newSta = seiscomp.datamodel.Station(sta)
                    newNet.add(newSta)

                    for k in range(sta.sensorLocationCount()):
                        loc = sta.sensorLocation(k)
                        if loc.code() != locCode:
                            continue

                        newLoc = seiscomp.datamodel.SensorLocation(loc)
                        newSta.add(newLoc)

                        for l in range(loc.streamCount()):
                            cha = loc.stream(l)
                            if cha.code() != chaCode:
                                continue

                            newCha = seiscomp.datamodel.Stream(cha)
                            newLoc.add(newCha)

                            dataloggers.add(cha.datalogger())
                            sensors.add(cha.sensor())

            responses = set()

            # datalogger
            for i in range(inv.dataloggerCount()):
                logger = inv.datalogger(i)
                if logger.publicID() not in dataloggers:
                    continue

                newLogger = seiscomp.datamodel.Datalogger(logger)
                newInv.add(newLogger)

                for j in range(logger.decimationCount()):
                    decimation = logger.decimation(j)
                    newLogger.add(seiscomp.datamodel.Decimation(decimation))

                    # collect response ids
                    filterStr = ""
                    try:
                        filterStr = decimation.analogueFilterChain().content() + " "
                    except ValueError:
                        pass

                    try:
                        filterStr += decimation.digitalFilterChain().content()
                    except ValueError:
                        pass

                    for resp in filterStr.split():
                        responses.add(resp)

            # sensor
            for i in range(inv.sensorCount()):
                sensor = inv.sensor(i)
                if sensor.publicID() not in sensors:
                    continue

                newSensor = seiscomp.datamodel.Sensor(sensor)
                newInv.add(newSensor)

                resp = newSensor.response()
                if resp:
                    responses.add(resp)

            # responses
            for i in range(inv.responsePAZCount()):
                resp = inv.responsePAZ(i)
                if resp.publicID() in responses:
                    newInv.add(seiscomp.datamodel.ResponsePAZ(resp))

            for i in range(inv.responseFIRCount()):
                resp = inv.responseFIR(i)
                if resp.publicID() in responses:
                    newInv.add(seiscomp.datamodel.ResponseFIR(resp))

            for i in range(inv.responsePolynomialCount()):
                resp = inv.responsePolynomial(i)
                if resp.publicID() in responses:
                    newInv.add(seiscomp.datamodel.ResponsePolynomial(resp))

            for i in range(inv.responseFAPCount()):
                resp = inv.responseFAP(i)
                if resp.publicID() in responses:
                    newInv.add(seiscomp.datamodel.ResponseFAP(resp))

            for i in range(inv.responseIIRCount()):
                resp = inv.responseIIR(i)
                if resp.publicID() in responses:
                    newInv.add(seiscomp.datamodel.ResponseIIR(resp))

            exporter = seiscomp.io.Exporter.Create("fdsnxml")

            if not exporter:
                raise Exception("Could not create fdsnxml exporter")

            bytesio = io.BytesIO()

            if not exporter.write(ExportSink(bytesio), newInv):
                raise Exception("Could not export fdsnxml")

            bytesio.seek(0)
            return obspy.read_inventory(bytesio, format="STATIONXML")

        finally:
            seiscomp.datamodel.Notifier.SetEnabled(notifierEnabled)
            seiscomp.datamodel.PublicObject.SetRegistrationEnabled(registrationEnabled)

class Me(object):
    def __init__(self, rsUrl, wcSize, icSize, yamlFile):
        self.__wc = WaveformCache(rsUrl, wcSize)
        self.__ic = InventoryCache(icSize)

        if yamlFile is None:
            yamlFile = os.path.dirname(__file__) + '/process.yaml'

        with open(yamlFile) as fp:
            self.__config = yaml.safe_load(fp)

    def compute(self, net, sta, loc, cha, mag, lat, lon, depth, arrivaltime):
        inv = self.__ic.get(net, sta, loc, cha)
        t = obspy.UTCDateTime(arrivaltime.iso())
        c = inv.get_coordinates("%s.%s.%s.%s" % (net, sta, loc, cha), t)
        dist = obspy.geodetics.locations2degrees(lat, lon, c['latitude'], c['longitude'])
        d = self.__config['freq_dist_table']['distances']

        if dist < d[0] or dist > d[-1]:
            raise SkipSegment('distance_deg=%f not in [%f, %f]' % (dist, d[0], d[-1]))
                                
        dur = get_segment_window_duration(mag, self.__config)
        wf = self.__wc.get(net, sta, loc, cha, t - dur, t + dur)

        if wf is None:
            raise SkipSegment('missing waveform')

        if len(wf) != 1:
            raise SkipSegment("%d traces (probably gaps/overlaps)" % len(wf))

        if wf[0].stats.starttime > t - dur or wf[0].stats.endtime < t + dur:
            raise SkipSegment('waveform=[%s, %s] not in [%s, %s]' %
                             (wf[0].stats.starttime, wf[0].stats.endtime,
                              t - dur, t + dur))

        return compute_me(wf, mag, depth, inv, t, dist, self.__config)

