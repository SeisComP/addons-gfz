#!/usr/bin/env seiscomp-python

import sys
import math
from seiscomp.core import Time, TimeSpan
from seiscomp.client import Application
from seiscomp.datamodel import Event, Origin, Magnitude, Pick, PublicObject
import seiscomp.logging
import mert

info    = seiscomp.logging.info
debug   = seiscomp.logging.info # XXX
warning = seiscomp.logging.warning
error   = seiscomp.logging.error

import warnings
warnings.showwarning = lambda message, category, filename, lineno, file=None, line=None: \
        warning(warnings.formatwarning(message, category, filename, lineno, line))


# Compound event with preferred origin/magnitude on board as well as some relevant state variables
class EventState:
    __slots__ = ("event", "origin", "magnitude", "pick", "preferredOriginID", "preferredMagnitudeID")

    def __init__(self, evt=None):
        self.event = evt
        self.origin = None
        self.magnitude = None
        self.pick = None
        self.preferredOriginID = None
        self.preferredMagnitudeID = None


class EventClient(Application):

    def __init__(self, argc, argv):
        Application.__init__(self, argc, argv)
        self.setMessagingEnabled(True)
        self.setDatabaseEnabled(True, True)
        self.addMessagingSubscription("EVENT")
        self.addMessagingSubscription("LOCATION")
        self.addMessagingSubscription("MAGNITUDE")
        self.addMessagingSubscription("PICK")
        self.setAutoApplyNotifierEnabled(True)

        # object buffers
        self._state = {}
        self._origin = {}
        self._magnitude = {}
        self._pick = {}

        self._cleanupCounter = 0
        self._xdebug = False
        self._cleanup_interval = 3600.


    def cleanup(self):
        self._cleanupCounter += 1
        if self._cleanupCounter < 5:
            return
        debug("before cleanup:")
        debug("   _state               %d" % len(self._state))
        debug("   _origin              %d" % len(self._origin))
        debug("   _magnitude           %d" % len(self._magnitude))
        debug("   _pick                %d" % len(self._pick))
        debug("   public object count  %d" % (PublicObject.ObjectCount()))
        # we first remove those origins and magnitudes, which are
        # older than one hour and are not preferred anywhere.
        limit = Time.GMT() + TimeSpan(-self._cleanup_interval)

        originIDs = list(self._origin.keys())
        for oid in originIDs:
            if self._origin[oid].creationInfo().creationTime() < limit:
                del self._origin[oid]

        magnitudeIDs = list(self._magnitude.keys())
        for oid in magnitudeIDs:
            if self._magnitude[oid] is None:
                # This should actually never happen!
                error("Magnitude %s is None!" % oid)
                del self._magnitude[oid]
                continue
            if self._magnitude[oid].creationInfo().creationTime() < limit:
                del self._magnitude[oid]

        pickIDs = list(self._pick.keys())
        for oid in pickIDs:
            if self._pick[oid].creationInfo().creationTime() < limit:
                del self._pick[oid]

        # finally remove all remaining objects older than two hours
        limit = Time.GMT() + TimeSpan(-2*self._cleanup_interval)
        to_delete = []
        for evid in self._state:
            org = self._state[evid].origin
            if org and org.time().value() > limit:
                continue # nothing to do with this event
            to_delete.append(evid)
        for evid in to_delete:
            del self._state[evid]

        debug("After cleanup:")
        debug("   _state               %d" % len(self._state))
        debug("   _origin              %d" % len(self._origin))
        debug("   _magnitude           %d" % len(self._magnitude))
        debug("   _pick                %d" % len(self._pick))
        debug("   public object count  %d" % (PublicObject.ObjectCount()))
        debug("-------------------------------")
        self._cleanupCounter = 0


    def changed_origin(self, event_id, previous_id, current_id):
        # to be implemented in a derived class
        raise NotImplementedError


    def changed_magnitude(self, event_id, previous_id, current_id):
        # to be implemented in a derived class
        raise NotImplementedError


    def _get_origin(self, oid):
        if oid not in self._origin:
             self._load_origin(oid)
        if oid in self._origin:
            return self._origin[oid]


    def _get_magnitude(self, oid):
        if oid not in self._magnitude:
             self._load_magnitude(oid)
        if oid in self._magnitude:
            return self._magnitude[oid]


    def _load(self, oid, tp):
        assert oid is not None
        debug("trying to load %s %s" % (str(tp), oid))
        tmp = tp.Cast(self.query().loadObject(tp.TypeInfo(), oid))
        if tmp:
            debug("loaded %s %s" % (tmp.ClassName(), oid))
        return tmp


    def _load_event(self, oid):
        evt = self._load(oid, Event)
        self._state[oid] = EventState(evt)
        # if we do this here, then we override the preferred* here and are not able to detect the difference!
        self._state[oid].origin = self._get_origin(evt.preferredOriginID())
        self._state[oid].magnitude = self._get_magnitude(evt.preferredMagnitudeID())


    def _load_origin(self, oid):
        self._origin[oid] = self._load(oid, Origin)


    def _load_magnitude(self, oid):
        self._magnitude[oid] = self._load(oid, Magnitude)


    def _load_pick(self, oid):
        self._pick[oid] = self._load(oid, Pick)


    def _process_event(self, evt):
        evid = evt.publicID()

        if self._xdebug:
            debug("_process_event %s start" % evid)

        st = self._state[evid]
        previous_preferredOriginID = st.preferredOriginID
        previous_preferredMagnitudeID = st.preferredMagnitudeID

        # possibly updated preferredOriginID/preferredMagnitudeID
        preferredOriginID = evt.preferredOriginID()
        preferredMagnitudeID = evt.preferredMagnitudeID()
        if not preferredOriginID:
            preferredOriginID = None
        if not preferredMagnitudeID:
            preferredMagnitudeID = None

        info("%s preferredOriginID         %s  %s" % (evid, previous_preferredOriginID, preferredOriginID))
        info("%s preferredMagnitudeID      %s  %s" % (evid, previous_preferredMagnitudeID, preferredMagnitudeID))


        # Test whether there have been any (for us!) relevant
        # changes in the event.
        if preferredOriginID is not None and preferredOriginID != previous_preferredOriginID:
            st.origin = self._get_origin(preferredOriginID)
            self.changed_origin(evid, previous_preferredOriginID, preferredOriginID)
            st.preferredOriginID = preferredOriginID

        if preferredMagnitudeID is not None and preferredMagnitudeID != previous_preferredMagnitudeID:
            st.magnitude = self._get_magnitude(preferredMagnitudeID)
            self.changed_magnitude(evid, previous_preferredMagnitudeID, preferredMagnitudeID)
            st.preferredMagnitudeID = preferredMagnitudeID

        self.cleanup()

        if self._xdebug:
            debug("_process_event %s end" % evid)


    def _process_origin(self, obj):
#       self.cleanup()
        pass # currently nothing to do here


    def _process_magnitude(self, obj):
#       self.cleanup()
        pass # currently nothing to do here


    def updateObject(self, parentID, updated):
        # called if an updated object is received
        for tp in [ Pick, Magnitude, Origin, Event ]:
            # try to convert to any of the above types
            obj = tp.Cast(updated)
            if obj:
                break

        if not obj:
            return

        oid = obj.publicID()

        if self._xdebug:
            debug("updateObject start %s  oid=%s" % (obj.ClassName(), oid))

        # our utility may have been offline during addObject, so we
        # need to check whether this is the first time that we see
        # this object. If that is the case, we load that object from
        # the database in order to be sure that we are working with
        # the complete object.
        if tp is Event:
            if oid in self._state:
                # *update* the existing instance - do *not* overwrite it!
                self._state[oid].event.assign(obj)
            else:
                self._load_event(oid)
            self._process_event(obj)

        elif tp is Origin:
            if oid in self._origin:
                # *update* the existing instance - do *not* overwrite it!
                self._origin[oid].assign(obj)
            else:
                self._load_origin(oid)
            self._process_origin(obj)

        elif tp is Magnitude:
            if oid in self._magnitude:
                # *update* the existing instance - do *not* overwrite it!
                self._magnitude[oid].assign(obj)
            else:
                self._load_magnitude(oid)
            self._process_magnitude(obj)

        elif tp is Pick:
            if oid in self._pick:
                # *update* the existing instance - do *not* overwrite it!
                self._pick[oid].assign(obj)
            else:
                self._load_pick(oid)
            #self._process_pick(obj)

        if self._xdebug:
            debug("updateObject end")


    def addObject(self, parentID, added):
        # called if a new object is received
        for tp in [ Pick, Magnitude, Origin, Event ]:
            obj = tp.Cast(added)
            if obj:
                break

        if not obj:
            return

        oid = obj.publicID()

        if self._xdebug:
            debug("addObject start %s  oid=%s" % (obj.ClassName(), oid))

        tmp = PublicObject.Find(oid)
        if not tmp:
            error("PublicObject.Find failed on %s %s" % (obj.ClassName(), oid))
            return
        # can we get rid of this?
        tmp = tp.Cast(tmp)
        tmp.assign(obj)
        obj = tmp

        if tp is Event:
            if oid not in self._state:
                self._state[oid] = EventState(obj)
                if obj.preferredOriginID():
                    self._state[oid].origin = self._get_origin(obj.preferredOriginID())
                if obj.preferredMagnitudeID():
                    self._state[oid].magnitude = self._get_magnitude(obj.preferredMagnitudeID())
            else:
                error("event %s already in self._state" % oid)
            self._process_event(obj)

        elif tp is Origin:
            if oid not in self._origin:
                self._origin[oid] = obj
            else:
                error("origin %s already in self._origin" % oid)
            self._process_origin(obj)

        elif tp is Magnitude:
            if oid not in self._magnitude:
                self._magnitude[oid] = obj
            else:
                error("magnitude %s already in self._magnitude" % oid)
            self._process_magnitude(obj)

        elif tp is Pick:
            if oid not in self._pick:
                self._pick[oid] = obj
            else:
                error("pick %s already in self._pick" % oid)
            #self._process_pick(obj)

        if self._xdebug:
            debug("addObject end")


def trimmedMean(values, percent):
    # Derived from:
    # https://github.com/SeisComP/common/blob/c2d63ec5226b879380d24f3dde7348ab47519b63/libs/seiscomp/math/mean.cpp#L129

    xl = percent * 0.005
    n = len(values)
    k = int(n * xl + 1e-5)
    cumv = cumw = cumd = 0
    dv = n*[0]
    weight = n*[0]

    for i, (j, v) in enumerate(sorted(enumerate(values),
                                      key=lambda a: a[1])):
        if k + 1 <= i < n - k - 1:
            weight[j] = 1

        elif i == k or i == n - k - 1:
            weight[j] = k + 1 - n * xl

        else:
            weight[j] = 0

        cumv += weight[j] * values[j]
        cumw += weight[j]

    v = cumv / cumw

    for i in range(n):
        dv[i] = values[i] - v
        cumd += weight[i] * dv[i] * dv[i];

    stdev = math.sqrt(cumd / (cumw - 1)) if cumw > 1 else None

    return v, stdev, dv, weight


class EventWatch(EventClient):

    def __init__(self, argc, argv):
        EventClient.__init__(self, argc, argv)
        reg = seiscomp.system.PluginRegistry.Instance()
        reg.addPluginName("fdsnxml")
        reg.loadPlugins()
        self.setLoadInventoryEnabled(True)
        self.setRecordStreamEnabled(True)
        self.__wcSize = 500
        self.__icSize = 500
        self.__minMag = 5.5
        self.__yamlFile = None

    def initConfiguration(self):
        if not seiscomp.client.Application.initConfiguration(self):
            return False

        try:
            self.__wcSize = self.configGetInt('waveformCacheSize')
        except Exception:
            pass

        try:
            self.__icSize = self.configGetInt('inventoryCacheSize')
        except Exception:
            pass

        try:
            self.__minMag = self.configGetDouble('magnitudeTreshold')
        except Exception:
            pass

        try:
            self.__yamlFile = self.configGetString('yamlConfig')
        except Exception:
            pass

        return True

    def init(self):
        EventClient.init(self)

        info("waveformCacheSize:  %d" % self.__wcSize)
        info("inventoryCacheSize: %d" % self.__icSize)
        info("magnitudeTreshold:  %f" % self.__minMag)
        info("yamlConfig:         %s" % self.__yamlFile)

        self.__me = mert.Me(self.recordStreamURL(), self.__wcSize, self.__icSize, self.__yamlFile)
        return True

    def __update(self, evid):
        s = self._state[evid]
        org = s.origin
        mag = s.magnitude
        info("EVT %s" % evid)
        if not org:
            return
        info("ORG %s" % (org.time().value()))
        if not mag:
            return
        info("MAG %.2f %s" % (mag.magnitude().value(), mag.type()))
        if mag.magnitude().value() < self.__minMag:
            return

        for i in range(org.stationMagnitudeCount()):
            mag = org.stationMagnitude(i)
            if mag.type() == "Me":
                return

        smList = []

        for i in range(org.arrivalCount()):
            arr = org.arrival(i)

            if arr.pickID() not in self._pick:
                self._load_pick(arr.pickID())

            try:
                pick = self._pick[arr.pickID()]

            except KeyError:
                error("pick %s not found" % arr.pickID())
                continue

            if pick.phaseHint().code()[0] != "P":
                continue

            wf = pick.waveformID()

            try:
                net = wf.networkCode()
                sta = wf.stationCode()
                loc = wf.locationCode()
                cha = wf.channelCode()
                me = self.__me.compute(net, sta, loc, cha, mag.magnitude().value(), org.latitude().value(), org.longitude().value(), org.depth().value(), pick.time().value())

                info("%s.%s.%s.%s Me = %f" % (net, sta, loc, cha, me))

            except Exception as e:
                info("%s.%s.%s.%s Error = %s" % (net, sta, loc, cha, str(e)))
                continue

            sm = seiscomp.datamodel.StationMagnitude.Create()
            sm.setOriginID(org.publicID())
            m = seiscomp.datamodel.RealQuantity()
            m.setValue(me)
            sm.setMagnitude(m)
            sm.setType("Me")
            wid = seiscomp.datamodel.WaveformStreamID()
            wid.setNetworkCode(net)
            wid.setStationCode(sta)
            wid.setLocationCode(loc)
            wid.setChannelCode(cha)
            sm.setWaveformID(wid)
            ci = seiscomp.datamodel.CreationInfo()
            ci.setCreationTime(seiscomp.core.Time().GMT())
            ci.setAgencyID(self.agencyID())
            ci.setAuthor(self.author())
            sm.setCreationInfo(ci)

            try:
                notifierEnabled = seiscomp.datamodel.Notifier.IsEnabled()
                seiscomp.datamodel.Notifier.SetEnabled(True)
                org.add(sm)

            finally:
                seiscomp.datamodel.Notifier.SetEnabled(notifierEnabled)

            smList.append(sm)

        if len(smList) > 0:
            percent = 25 # XXX: hardcoded
            values = [sm.magnitude().value() for sm in smList]
            (mean, stdev, residual, weight) = trimmedMean(values, percent)
            mag = seiscomp.datamodel.Magnitude.Create()
            mag.setOriginID(org.publicID())
            m = seiscomp.datamodel.RealQuantity()
            m.setValue(mean)
            m.setUncertainty(stdev)
            mag.setMagnitude(m)
            mag.setType("Me")
            mag.setMethodID("trimmed mean(%d)" % percent)
            mag.setStationCount(sum(w>0 for w in weight))
            ci = seiscomp.datamodel.CreationInfo()
            ci.setCreationTime(seiscomp.core.Time().GMT())
            ci.setAgencyID(self.agencyID())
            ci.setAuthor(self.author())
            mag.setCreationInfo(ci)

            try:
                notifierEnabled = seiscomp.datamodel.Notifier.IsEnabled()
                seiscomp.datamodel.Notifier.SetEnabled(True)
                org.add(mag)

            finally:
                seiscomp.datamodel.Notifier.SetEnabled(notifierEnabled)

            for i, sm in enumerate(smList):
                smc = seiscomp.datamodel.StationMagnitudeContribution()
                smc.setStationMagnitudeID(sm.publicID())
                smc.setResidual(residual[i])
                smc.setWeight(weight[i])

                try:
                    notifierEnabled = seiscomp.datamodel.Notifier.IsEnabled()
                    seiscomp.datamodel.Notifier.SetEnabled(True)
                    mag.add(smc)

                finally:
                    seiscomp.datamodel.Notifier.SetEnabled(notifierEnabled)

        msg = seiscomp.datamodel.Notifier.GetMessage(True)
        if msg:
            self.connection().send("MAGNITUDE", msg)

        return

    def changed_origin(self, event_id, previous_id, current_id):
        debug("EventWatch.changed_origin")
        debug("event %s: CHANGED preferredOriginID" % event_id)
        debug("    from %s" % previous_id)
        debug("      to %s" % current_id)
        self.__update(event_id)

    def changed_magnitude(self, event_id, previous_id, current_id):
        debug("EventWatch.changed_magnitude")
        debug("event %s: CHANGED preferredMagnitudeID" % event_id)
        debug("    from %s" % previous_id)
        debug("      to %s" % current_id)
        self.__update(event_id)

if __name__ == "__main__":
    app = EventWatch(len(sys.argv), sys.argv)
    sys.exit(app())

