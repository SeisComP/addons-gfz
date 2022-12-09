"""

"""
import numpy as np
from scipy.interpolate import CubicSpline
from scipy.constants import pi
from os.path import abspath, splitext
from obspy import Stream, UTCDateTime

from .stream2segment.ndarrays import snr, triangsmooth
from .stream2segment.traces import (ampratio, bandpass, ampspec, powspec,
                                    sn_split)
import yaml


class SkipSegment(Exception):
    """Implement a SkipSegment exception (legacy code of Stream2segment)"""
    pass


def compute_me(waveform, mag, event_depth_km, inventory, arrival_time,
               event_distance_deg, config=None):
    """
    Compute the Magnitude energy for the given waveform

    :param waveform: an `obspy.Stream` object with a single Trace (no gaps)
        registering the waveform vertical (Z) component. In case of more Traces,
        `SkipSegment` is raised
    :param mag: the event magnitude
    :param event_depth_km: the event depth, in Km
    :param inventory: the waveform station inventory, as
        obspy.core.inventory.inventory.Inventory object
    :param arrival_time: the arrival time, as datetime or obspy UTCDateTime
        object. It must be a pre computed arrival time and must be included
        between the waveform start time and end time
    :param event_distance_deg: the distance between
    :param config: a dict with the configuration needed. If None, it will be
        loaded from the `process.yaml` asociated file. In case of multiple runs,
        it might be convenient to load once the configuration and call this
        function for each waveform

    :return: a float denoting the computed Magnitude Energy

    :raise: SkipSegment and most likely ValueError or TypeError (depending on
        the potential exceptions of the underlying obspy library)
    """
    ############################################
    # CHECKS (only one trace, not saturated).. #
    ############################################

    if len(waveform) != 1:
        raise SkipSegment("%d traces (probably gaps/overlaps)" % len(waveform))

    trace = waveform[0]

    if config is None:
        with open(splitext(abspath(__file__))[0] + '.yaml') as _:
            config = yaml.safe_load(_)

    # discard saturated signals (according to the threshold set in the config file):
    if config.get('amp_ratio_threshold', 0):
        amp_ratio = ampratio(trace)
        if amp_ratio >= config['amp_ratio_threshold']:
            raise SkipSegment("Trace looks saturated")

    ############
    # Start Me #
    ############

    # bandpass the trace, according to the event magnitude.
    # WARNING: this modifies the segment.stream() permanently!
    # If you want to preserve the original stream, store trace.copy()
    # or use segment.stream(True). Note that bandpass function assures the trace is one
    # (no gaps/overlaps)
    try:
        if not hasattr(trace, 'remresp'):
            trace = bandpass_remresp(trace, inventory, config)
            trace.remresp = True
    except (ValueError, TypeError) as exc:
        raise SkipSegment('error in bandpass_remresp: %s' % str(exc))

    spectra = signal_noise_spectra(trace, arrival_time, mag, config)
    normal_f0, normal_df, normal_spe = spectra['Signal']
    noise_f0, noise_df, noise_spe = spectra['Noise']

    # For future developments, we might use konno ohmaci

    fcmin = 0.001  # 20s, integration for Energy starts from 16s
    fcmax = config['preprocess']['bandpass_freq_max']  # used in bandpass_remresp
    snr_ = snr(normal_spe, noise_spe, signals_form=config['sn_spectra']['type'],
               fmin=fcmin, fmax=fcmax, delta_signal=normal_df, delta_noise=noise_df)
    
    if snr_ < config['snr_threshold']:
        # NOTE: we get here quite often, maybe just return None and skip logging?
        raise SkipSegment('snr %f < %f' % (snr_, config['snr_threshold']))

    ##################
    # ME COMPUTATION #
    ##################

    normal_spe *= trace.stats.delta

    duration = get_segment_window_duration(mag, config)

    if duration == 60:
        freq_min_index = 1  # 0.015625 (see frequencies in yaml)
    else:
        freq_min_index = 0  # 0.012402 (see frequencies in yaml)

    freq_dist_table = config['freq_dist_table']
    frequencies = freq_dist_table['frequencies'][freq_min_index:]
    distances = freq_dist_table['distances']
    try:
        distances_table = freq_dist_table[duration]
    except KeyError:
        raise KeyError(f'no freq dist table implemented for {duration} seconds')

    # unnecessary asserts just used for testing (comment out):
    # assert sorted(distances) == distances
    # assert sorted(frequencies) == frequencies

    # calculate spectra with spline interpolation on given frequencies:
    normal_freqs = np.linspace(normal_f0,
                               normal_f0 + len(normal_spe) * normal_df,
                               num=len(normal_spe), endpoint=True)
    try:
        cs = CubicSpline(normal_freqs, normal_spe)
    except ValueError as verr:
        raise SkipSegment('Error in CubicSpline: %s' % str(verr))

    seg_spectrum = cs(frequencies)

    seg_spectrum_log10 = np.log10(seg_spectrum)

    distance_deg = event_distance_deg
    if distance_deg < distances[0] or distance_deg > distances[-1]:
        raise SkipSegment('Passed `distance_deg`=%f not in [%f, %f]' %
                         (distance_deg, distances[0], distances[-1]))

    distindex = np.searchsorted(distances, distance_deg)

    if distances[distindex] == distance_deg:
        correction_spectrum_log10 = distances_table[distindex]
    else:
        distances_table = np.array(distances_table).reshape(len(distances),
                                                            len(frequencies))
        css = [CubicSpline(distances, distances_table[:, i])
               for i in range(len(frequencies))]
        correction_spectrum_log10 = [css[freqindex](distance_deg)
                                     for freqindex in range(len(frequencies))]

    corrected_spectrum = seg_spectrum_log10 - correction_spectrum_log10

    corrected_spectrum = np.power(10, corrected_spectrum) ** 2  # convert log10A -> A^2:
    
    corrected_spectrum_int_vel_square = np.trapz(corrected_spectrum, frequencies)

    depth_km = event_depth_km
    if depth_km < 10:
        v_dens = 2800
        v_pwave = 6500
        v_swave = 3850
    elif depth_km < 18:
        v_dens = 2920
        v_pwave = 6800
        v_swave = 3900
    else:
        v_dens = 3641
        v_pwave = 8035.5
        v_swave = 4483.9
    
    v_cost_p = (1. /(15. * pi * v_dens * (v_pwave ** 5)))
    v_cost_s = (1. /(10. * pi * v_dens * (v_swave ** 5)))
    # below I put a factor 2 but ... we don't know yet if it is needed
    energy = 2 * (v_cost_p + v_cost_s) * corrected_spectrum_int_vel_square
    me_st = (2./3.) * (np.log10(energy) - 4.4)

    # END OF ME COMPUTATION =============================================

    ###########################
    # AMPLITUDE ANOMALY SCORE #
    ###########################

    # (if uncommented, it should be done on the unprocessed trace before response
    # removal or bandpas)

    # try:
    #     aascore = trace_score(trace, segment.inventory())
    # except Exception as exc:
    #     aascore = np.nan

    return me_st


def bandpass_remresp(trace, inventory, config):
    """Applies a pre-process on the given segment waveform by
    filtering the signal and removing the instrumental response.
    DOES modify the segment stream in-place (see below).

    The filter algorithm has the following steps:
    1. Sets the max frequency to 0.9 of the Nyquist frequency (sampling rate /2)
    (slightly less than Nyquist seems to avoid artifacts)
    2. Offset removal (subtract the mean from the signal)
    3. Tapering
    4. Pad data with zeros at the END in order to accommodate the filter transient
    5. Apply bandpass filter, where the lower frequency is set according to the magnitude
    6. Remove padded elements
    7. Remove the instrumental response

    IMPORTANT NOTES:
    - Being decorated with '@gui.preprocess', this function:
      * returns the *base* stream used by all plots whenever the relative check-box is on
      * must return either a Trace or Stream object

    - In this implementation THIS FUNCTION DOES MODIFY `segment.stream()` IN-PLACE: from
     within `main`, further calls to `segment.stream()` will return the stream returned
     by this function. However, In any case, you can use `segment.stream().copy()` before
     this call to keep the old "raw" stream

    :return: a Trace object.
    """
    conf = config['preprocess']
    # note: bandpass here below modified the trace inplace
    trace = bandpass(trace, freq_min = 0.02, freq_max=conf['bandpass_freq_max'],
                     max_nyquist_ratio=conf['bandpass_max_nyquist_ratio'],
                     corners=conf['bandpass_corners'], copy=False)
    trace.remove_response(inventory=inventory, output=conf['remove_response_output'],
                          water_level=conf['remove_response_water_level'])
    return trace


def signal_noise_spectra(trace, arrival_time, mag, config):
    """Compute the signal and noise spectra, as dict of strings mapped to
    tuples (x0, dx, y). Does not modify the segment's stream or traces in-place

    :return: a dict with two keys, 'Signal' and 'Noise', mapped respectively to
        the tuples (f0, df, frequencies)

    :raise: an Exception if `segment.stream()` is empty or has more than one
        trace (possible gaps/overlaps)
    """
    # (this function assumes stream has only one trace)
    atime_shift = config['sn_windows']['arrival_time_shift']
    arrival_time = UTCDateTime(arrival_time) + atime_shift
    duration = get_segment_window_duration(mag, config)
    signal_trace, noise_trace = sn_split(trace, arrival_time, duration)

    signal_trace.taper(0.05, type='cosine')
    dura_sec = signal_trace.stats.delta * (8192-1)
    signal_trace.trim(starttime=signal_trace.stats.starttime,
                      endtime=signal_trace.stats.endtime+dura_sec, pad=True,
                      fill_value=0)
    dura_sec = noise_trace.stats.delta * (8192-1)
    noise_trace.taper(0.05, type='cosine')
    noise_trace.trim(starttime=noise_trace.stats.starttime,
                     endtime=noise_trace.stats.endtime+dura_sec, pad=True,
                     fill_value=0)

    x0_sig, df_sig, sig = _spectrum(signal_trace, config)
    x0_noi, df_noi, noi = _spectrum(noise_trace, config)

    return {'Signal': (x0_sig, df_sig, sig), 'Noise': (x0_noi, df_noi, noi)}


def get_segment_window_duration(magnitude, config):
    magrange2duration = config['magrange2duration']
    for m in magrange2duration:
        if m[0] <= magnitude < m[1]:
            return m[2]
    return 90


def _spectrum(trace, config, starttime=None, endtime=None):
    """Calculate the spectrum of a trace. Returns the tuple (0, df, values),
    where values depends on the config dict parameters.
    Does not modify the trace in-place
    """
    taper_max_percentage = config['sn_spectra']['taper']['max_percentage']
    taper_type = config['sn_spectra']['taper']['type']
    if config['sn_spectra']['type'] == 'pow':
        func = powspec  # copies the trace if needed
    elif config['sn_spectra']['type'] == 'amp':
        func = ampspec  # copies the trace if needed
    else:
        # raise TypeError so that if called from within main, the iteration stops
        raise TypeError("config['sn_spectra']['type'] expects either 'pow' or 'amp'")

    df_, spec_ = func(trace, starttime, endtime,
                      taper_max_percentage=taper_max_percentage,
                      taper_type=taper_type)

    # if you want to implement your own smoothing, change the lines below before
    # 'return' and implement your own config variables, if any
    smoothing_wlen_ratio = config['sn_spectra']['smoothing_wlen_ratio']
    if smoothing_wlen_ratio > 0:
        spec_ = triangsmooth(spec_, winlen_ratio=smoothing_wlen_ratio)
        # normal_freqs = 0. + np.arange(len(spec_)) * df_
        # spec_ = konno_ohmachi_smoothing(spec_, normal_freqs, bandwidth=60,
        #         count=1, enforce_no_matrix=False, max_memory_usage=512,
        #         normalize=False)

    return 0, df_, spec_
