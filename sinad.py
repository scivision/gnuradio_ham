#!/usr/bin/env python3

import numpy as np
from gnuradio import gr


class Sinad(gr.sync_block):
    """
    Simulate SINAD (Signal to Noise and Distortion) measurement for a given
    frequency and sample rate.
    """

    def __init__(self, sinadFreq, Fs):
        gr.sync_block.__init__(self, "sinad_ff", [np.float32], [np.float32])
        self.Fs = Fs
        self.fRef = sinadFreq
        self.refWidth = 20
        self.last_sinad = 0.0
        self.min_sinad_db = -3.0
        self.max_sinad_db = 30.0
        self.max_step_db = 2.0

    def work(self, input_items, output_items):
        sinad_val = self.__calc_sinad(input_items[0])
        if not np.isfinite(sinad_val):
            sinad_val = self.last_sinad
        else:
            # Reject one-block spikes by limiting slew between adjacent estimates.
            delta = sinad_val - self.last_sinad
            if delta > self.max_step_db:
                sinad_val = self.last_sinad + self.max_step_db
            elif delta < -self.max_step_db:
                sinad_val = self.last_sinad - self.max_step_db

            sinad_val = float(np.clip(sinad_val, self.min_sinad_db, self.max_sinad_db))
            self.last_sinad = sinad_val

        output_items[0][:] = sinad_val
        return len(output_items[0])

    def __calc_sinad(self, data):
        """ Takes float array and returns the sinad in dB
        """
        # %% compute FFT

        # 5/9/07 - added data windowing - ASP
        data_f = np.asarray(data, dtype=np.float32)
        window = np.hamming(data_f.size).astype(np.float32)
        windowed = data_f * window

        psd = np.fft.fft(windowed)
        psd = psd.flatten()

        # %% determine bin indices
        bin1 = (
            int(np.floor(float((self.fRef - self.refWidth / 2)) / float(self.Fs) * psd.shape[0]))
            - 1
        )
        bin2 = (
            int(np.ceil(float((self.fRef + self.refWidth / 2)) / float(self.Fs) * psd.shape[0]))
            - 1
        )

        # 4/9/07 - added filtering 300 - 3000Hz ASP

        bin300hz = int(np.floor(float(300) / float(self.Fs) * psd.shape[0])) - 1
        bin3000hz = int(np.floor(float(3000) / float(self.Fs) * psd.shape[0])) - 1

        # Keep bins in valid range to avoid empty/invalid slices.
        n = psd.shape[0]
        bin300hz = max(0, min(bin300hz, n - 1))
        bin3000hz = max(bin300hz + 1, min(bin3000hz, n))
        bin1 = max(bin300hz, min(bin1, bin3000hz - 1))
        bin2 = max(bin1 + 1, min(bin2, bin3000hz))

        # %% calculate SINAD = 10*log10(Ps/Pn)
        # 4/9/07 change signal = psd[bin1:bin2] to psd[bin300hz:bin3000hz] - ASP
        signal = psd[bin300hz:bin3000hz]
        noise = np.concatenate((psd[bin300hz:bin1], psd[(bin2 + 1):bin3000hz]))

        # Ps1 = float(sum(signal.real*signal.real + signal.imag*signal.imag)) #15/8 DON
        # Pn1 = float(sum(noise.real*noise.real + noise.imag*noise.imag))     #15/8 DON

        Ps = float(np.sum(np.abs(signal) ** 2))
        Pn = float(np.sum(np.abs(noise) ** 2))

        # Guard against numerical edge cases while preserving negative SINAD values.
        eps = 1e-20
        ratio = (Ps + eps) / (Pn + eps)
        sinad = 10.0 * np.log10(ratio)

        return sinad
