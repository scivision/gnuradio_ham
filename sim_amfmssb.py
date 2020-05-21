#!/usr/bin/env python3
from math import log10

from gnuradio import audio, analog, blocks, filter, gr
from gnuradio.eng_option import eng_option
from gnuradio.filter import firdes
from optparse import OptionParser
import sip
import sys

try:
    from PyQt5 import Qt
    from gnuradio import qtgui
    from gnuradio.qtgui import Range, RangeWidget
except ImportError:
    Qt = qtgui = Range = RangeWidget = None
#
from sinad import sinad_ff

v2dbm = 316.18e-3  # V

modtype = "fm"


def common_params(self):
    """
    these are the default parameters, some of which are GUI-adjustable
    """
    self.samp_rate = 192000
    self.fs_audio = 48000
    # %% transmitter

    self.tonelevel_dB = -4.77
    self._tonelevel_dB_range = Range(-60, 0, 1, -4.77, 200)
    self._tonelevel_dB_win = RangeWidget(
        self._tonelevel_dB_range, self.set_tonelevel_dB, "Mod. Level [dB]", "counter_slider", float
    )
    self.top_layout.addWidget(self._tonelevel_dB_win)

    self.Ptx_dBm = 30.0
    self._Ptx_dBm_range = Range(-30, 60, 1, 30, 200)
    self._Ptx_dBm_win = RangeWidget(
        self._Ptx_dBm_range, self.set_Ptx_dBm, "Transmit Power [dBm]", "counter_slider", float
    )
    self.top_layout.addWidget(self._Ptx_dBm_win)

    self.ftx = 50e3
    # %% propagation
    self.pathloss_dB = 140
    # %% receiver
    self.bwrx = 45000  # [hz]
    self.fmbw = 15000  # [hz]
    self.nf = 8  # [dB] noise figure

    self.frx = 50e3
    self._frx_range = Range(40e3, 60e3, 0.1e3, 50e3, 200)
    self._frx_win = RangeWidget(
        self._frx_range, self.set_frx, "RX freq. [Hz]", "counter_slider", float
    )
    self.top_layout.addWidget(self._frx_win)

    self.rx_decim = self.samp_rate / self.fs_audio

    self.Anoise = 10 ** ((-174.4 + 10 * log10(self.bwrx) + self.nf) / 20)

    self.snr = None


def module_setup(self):
    # %% transmitter
    self.mod_upsample = filter.rational_resampler_ccc(
        interpolation=self.samp_rate // self.fs_audio, decimation=1, taps=None, fractional_bw=None
    )

    self.mod_source = analog.sig_source_f(
        self.fs_audio, analog.GR_SIN_WAVE, 1000, 10 ** (self.tonelevel_dB / 20), 0
    )

    if modtype == "am":
        self.mod_out = blocks.float_to_complex()
        self.tx_carrier_level = blocks.add_const_ff(1)  # normalized
    elif modtype == "fm":
        self.mod_out = analog.nbfm_tx(
            audio_rate=self.fs_audio, quad_rate=self.samp_rate, tau=75e-6, max_dev=self.fmbw
        )
    elif modtype == "ssb":
        self.hilb = filter.hilbert_fc(256, firdes.WIN_HAMMING, 6.76)

    self.TX_LO = analog.sig_source_c(self.samp_rate, analog.GR_SIN_WAVE, self.ftx, 1, 0)

    self.tx_mixer = blocks.multiply_cc()

    self.transmit_amplifier = blocks.multiply_const_cc(v2dbm * 10 ** (self.Ptx_dBm / 20))
    # %% propagation, noise, interference
    self.pathloss = blocks.multiply_const_cc(10 ** (-self.pathloss_dB / 20))

    self.thermal_noise = analog.noise_source_c(analog.GR_GAUSSIAN, self.Anoise, 0)

    self.channeladder = blocks.add_cc()
    # %% receiver
    self.rx_lo = analog.sig_source_c(self.samp_rate, analog.GR_SIN_WAVE, -self.frx, 1, 0)

    self.rx_mixer = blocks.multiply_cc()

    self.rx_downsample = filter.rational_resampler_ccc(
        interpolation=1, decimation=self.samp_rate // self.fs_audio, taps=None, fractional_bw=None
    )

    if modtype == "am":
        self.rxif_filter = filter.fir_filter_ccc(
            1, firdes.low_pass(1, self.fs_audio, self.bwrx / 2, 200, firdes.WIN_HAMMING, 6.76)
        )

        self.agc = analog.agc2_cc(attack_rate=1, decay_rate=1, reference=0.9, gain=100)
        self.agc.set_max_gain(65535)

        self.demod = blocks.complex_to_mag()  # envelope det
    elif modtype == "fm":
        self.rxif_filter = filter.fir_filter_ccc(
            1, firdes.low_pass(1, self.fs_audio, self.bwrx / 2, 200, firdes.WIN_HAMMING, 6.76)
        )

        self.demod = analog.nbfm_rx(
            audio_rate=self.fs_audio, quad_rate=self.fs_audio, tau=75e-6, max_dev=self.fmbw
        )
    elif modtype == "ssb":
        self.rxif_filter = filter.fir_filter_ccc(
            1,
            firdes.band_pass(
                1, self.samp_rate, self.frx + 300, self.frx + 4000, 2500, firdes.WIN_HAMMING, 6.76
            ),
        )

        self.agc = analog.agc2_cc(attack_rate=1, decay_rate=1, reference=0.9, gain=100)
        self.agc.set_max_gain(65535)

        self.demod = blocks.complex_to_float()

    self.audio_bpf = filter.fir_filter_fff(
        1, firdes.band_pass(1, self.fs_audio, 300, 3400, 200, firdes.WIN_HAMMING, 6.76)
    )

    self.audio_out = audio.sink(self.fs_audio, "", False)


def text(tl, snr):
    txt = Qt.QToolBar()

    if None:
        _snr_formatter = None
    else:

        def _snr_formatter(x):
            return x

    txt.addWidget(Qt.QLabel("snr" + ": "))
    _snr_label = Qt.QLabel(str(_snr_formatter(snr)))
    txt.addWidget(_snr_label)
    tl.addWidget(txt)
    return txt


def scope(name, fsamp, tl, vpk, ntype, autoscale=False):

    if ntype is complex:
        scope = qtgui.time_sink_c(
            1024, fsamp, name, 1  # size  # samp_rate  # name  # number of inputs
        )
    else:
        scope = qtgui.time_sink_f(
            1024, fsamp, name, 1  # size  # samp_rate  # name  # number of inputs
        )
    scope.set_update_time(0.10)
    if isinstance(vpk, (tuple, list)):
        scope.set_y_axis(vpk[0], vpk[1])
    else:
        scope.set_y_axis(-vpk, vpk)

    scope.set_y_label("Amplitude", "Volts")
    scope.enable_tags(-1, True)
    scope.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, 0, "")
    scope.enable_autoscale(autoscale)
    scope.enable_grid(True)
    scope.enable_control_panel(False)
    #   self.scope_tx.disable_legend()

    _scope_win = sip.wrapinstance(scope.pyqwidget(), Qt.QWidget)
    tl.addWidget(_scope_win)
    return scope


def specan(name, fsamp, tl, ntype, autoscale=True, minmax=(-180, 0)):
    if isinstance(ntype, complex):
        specan = qtgui.freq_sink_c(
            1024,  # size
            firdes.WIN_RECTANGULAR,  # wintype
            0,  # fc
            fsamp,  # bw
            name,  # name
            1,  # number of inputs
        )
    else:
        specan = qtgui.freq_sink_f(
            1024,  # size
            firdes.WIN_RECTANGULAR,  # wintype
            0,  # fc
            fsamp,  # bw
            name,  # name
            1,  # number of inputs
        )

    specan.set_update_time(0.10)
    specan.set_y_axis(minmax[0], minmax[1])
    specan.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
    specan.enable_autoscale(autoscale)
    specan.enable_grid(True)
    specan.set_fft_average(1.0)
    specan.enable_control_panel(True)
    #        specan.disable_legend()

    if isinstance(complex, float):
        specan.set_plot_pos_half(not True)

    _specan_win = sip.wrapinstance(specan.pyqwidget(), Qt.QWidget)
    tl.addWidget(_specan_win)
    return specan


class top_block(gr.top_block, Qt.QWidget):
    def __init__(self):
        gr.top_block.__init__(self, "Top Block")
        Qt.QWidget.__init__(self)
        self.setWindowTitle("Top Block")
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme("gnuradio-grc"))
        except Exception:
            pass
        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("GNU Radio", "top_block")
        #       self.restoreGeometry(self.settings.value("geometry").toByteArray())
        # %% setup radio
        common_params(self)
        module_setup(self)
        # %% Connect transmitter
        self.connect(self.TX_LO, (self.tx_mixer, 0))

        if modtype == "am":
            self.connect(
                self.mod_source,
                self.tx_carrier_level,
                self.mod_out,
                self.mod_upsample,
                (self.tx_mixer, 1),
            )
        elif modtype == "fm":
            self.connect(self.mod_source, self.mod_out, (self.tx_mixer, 1))
        elif modtype == "ssb":
            self.connect(self.mod_source, self.hilb, self.mod_upsample, (self.tx_mixer, 1))

        self.connect(self.tx_mixer, self.transmit_amplifier, self.pathloss)
        # %% connect channel model
        self.connect(self.pathloss, (self.channeladder, 0))
        self.connect(self.thermal_noise, (self.channeladder, 1))
        # %% connect receiver
        self.connect(self.rx_lo, (self.rx_mixer, 0))

        if modtype == "am":
            self.connect(self.channeladder, (self.rx_mixer, 1))
            self.connect(
                self.rx_mixer,
                self.rx_downsample,
                self.rxif_filter,
                self.agc,
                self.demod,
                self.audio_bpf,
                self.audio_out,
            )
        elif modtype == "fm":
            self.connect(self.channeladder, (self.rx_mixer, 1))
            self.connect(
                self.rx_mixer,
                self.rx_downsample,
                self.rxif_filter,
                self.demod,
                self.audio_bpf,
                self.audio_out,
            )
        elif modtype == "ssb":
            self.connect(self.channeladder, self.rxif_filter, (self.rx_mixer, 1))
            self.connect(
                self.rx_mixer,
                self.rx_downsample,
                self.agc,
                (self.demod, 0),
                self.audio_bpf,
                self.audio_out,
            )

        # %% SNR
        # only SVR observed to work and only up to 18dB SNR. Need to use sinad instead, or quieting ratio.

        #        self._snr_tool_bar = Qt.QToolBar(self)
        #
        #        if None:
        #          self._snr_formatter = None
        #        else:
        #          self._snr_formatter = lambda x: x
        #
        #        self._snr_tool_bar.addWidget(Qt.QLabel("snr"+": "))
        #        self._snr_label = Qt.QLabel(str(self._snr_formatter(self.snr)))
        #        self._snr_tool_bar.addWidget(self._snr_label)
        #        self.top_layout.addWidget(self._snr_tool_bar)
        ################
        # self.snr_est = digital.probe_mpsk_snr_est_c(digital.SNR_EST_SVR, self.samp_rate//10, 0.001)
        # self.connect(self.rx_downsample, self.snr_est)
        ################
        #       text(self.top_layout,self.snr_est)

        #        def _snr_disp_probe():
        #            while True:
        #                val = self.snr_est.snr()
        #                try:
        #                    self.set_snr_disp('{:.2f}'.format(val))
        #                except AttributeError:
        #                    pass
        #                time.sleep(1.0 / (2))
        #        _snr_disp_thread = threading.Thread(target=_snr_disp_probe)
        #        _snr_disp_thread.daemon = True
        #        _snr_disp_thread.start()
        # %% SINAD
        # TODO make probe instead of stream
        self.sinad_est = sinad_ff(1000, self.fs_audio)
        self.connect(self.audio_bpf, self.sinad_est)
        scope_sinad = scope("SINAD", self.fs_audio, self.top_layout, (0, 50), float)
        self.connect(self.sinad_est, scope_sinad)

        #        self.sinad_probe = blocks.probe_signal_f()
        #        sinad_ff(1000,self.fs_audio)
        #        def _sinad_probe():
        #            while True:
        #                sinad = self.sinad_probe.level()
        #                print(sinad)
        #                time.sleep(0.5)
        #
        #        #setup thread
        #        _sinad_thread=threading.Thread(target=_sinad_probe)
        #        _sinad_thread.daemon = True
        #        _sinad_thread.start()
        #
        #        self.connect(self.audio_bpf,self.sinad_probe)

        # %% plots
        if 0:
            scope_tx = scope("TX output", self.samp_rate, self.top_layout, v2dbm, complex)
            self.connect(self.transmit_amplifier, scope_tx)

        if 0:
            scope_rxant = scope("RX input", self.samp_rate, self.top_layout, 2e-6, complex)
            self.connect(self.channeladder, scope_rxant)

        if 0:
            scope_rxif_filter = scope(
                "RX IF filtered", self.samp_rate, self.top_layout, 2e-6, complex
            )
            self.connect(self.rxif_filter, scope_rxif_filter)

        if 1:

            if modtype in ("am", "ssb"):
                specan_rxif = specan(
                    "RX IF", self.samp_rate, self.top_layout, complex, False, (-180, -100)
                )
                self.connect(self.rxif_filter, specan_rxif)
            else:
                specan_rxif = specan(
                    "RX IF", self.fs_audio, self.top_layout, complex, False, (-180, -100)
                )
                self.connect(self.rx_downsample, specan_rxif)

        if 0:
            scope_rxaudio = scope("RX audio", self.samp_rate, self.top_layout, v2dbm, float)
            self.connect(self.audio_bpf, scope_rxaudio)

        if 1:
            specan_rxaudio = specan(
                "RX audio", self.fs_audio, self.top_layout, float, False, (-100, 0)
            )
            self.connect(self.audio_bpf, specan_rxaudio)

    # %%

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "top_block")
        self.settings.setValue("geometry", self.saveGeometry())
        event.accept()

    # %% functions -- keep these for current/future GUI that GNURadio will call

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.set_rx_decim(self.samp_rate / self.fs_audio)
        self.TX_LO.set_sampling_freq(self.samp_rate)
        self.scope_tx.set_samp_rate(self.samp_rate)
        self.snr_est.set_msg_nsample(self.samp_rate / 10)

    def get_fs_audio(self):
        return self.fs_audio

    def set_fs_audio(self, fs_audio):
        self.fs_audio = fs_audio
        self.set_rx_decim(self.samp_rate / self.fs_audio)
        self.mod_source.set_sampling_freq(self.fs_audio)

    def get_tonelevel_dB(self):
        return self.tonelevel_dB

    def set_tonelevel_dB(self, tonelevel_dB):
        self.tonelevel_dB = tonelevel_dB
        self.mod_source.set_amplitude(10 ** (self.tonelevel_dB / 20))  # normalized

    def get_rx_decim(self):
        return self.rx_decim

    def set_rx_decim(self, rx_decim):
        self.rx_decim = rx_decim

    def get_pathloss_dB(self):
        return self.pathloss_dB

    def set_pathloss_dB(self, pathloss_dB):
        self.pathloss_dB = pathloss_dB

    def get_ftx(self):
        return self.ftx

    def set_ftx(self, ftx):
        self.ftx = ftx
        self.TX_LO.set_frequency(self.ftx)

    def get_frx(self):
        return self.frx

    def set_frx(self, frx):
        self.frx = frx
        self.rx_lo.set_frequency(-self.frx)

    def get_Ptx_dBm(self):
        return self.Ptx_dBm

    def set_Ptx_dBm(self, Ptx_dBm):
        self.Ptx_dBm = Ptx_dBm
        self.transmit_amplifier.set_k(v2dbm * 10 ** (self.Ptx_dBm / 20))

    def get_Anoise(self):
        return self.Anoise

    def set_Anoise(self, Anoise):
        self.Anoise = Anoise

    def get_snr(self):
        return self.snr

    def set_snr(self, snr):
        self.snr = snr
        Qt.QMetaObject.invokeMethod(self._snr_label, "setText", Qt.Q_ARG("QString", str(self.snr)))

    def get_snr_disp(self):
        return self.snr_disp

    def set_snr_disp(self, snr_disp):
        self.snr_disp = snr_disp
        self.set_snr(self._snr_formatter(self.snr_disp))


if __name__ == "__main__":
    import ctypes

    if sys.platform.startswith("linux"):
        try:
            x11 = ctypes.cdll.LoadLibrary("libX11.so")
            x11.XInitThreads()
        except Exception:
            print("Warning: failed to XInitThreads()")

    parser = OptionParser(option_class=eng_option, usage="%prog: [options]")
    (options, args) = parser.parse_args()
    from distutils.version import StrictVersion

    if StrictVersion(Qt.qVersion()) >= StrictVersion("4.5.0"):
        Qt.QApplication.setGraphicsSystem(gr.prefs().get_string("qtgui", "style", "raster"))
    qapp = Qt.QApplication(sys.argv)
    tb = top_block()
    tb.start()
    tb.show()

    def quitting():
        tb.stop()
        tb.wait()

    qapp.connect(qapp, Qt.SIGNAL("aboutToQuit()"), quitting)
    qapp.exec_()
    tb = None  # to clean up Qt widgets
