#!/usr/bin/env python2
#
# Copyright 2013 <+YOU OR YOUR COMPANY+>.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software; see the file COPYING.  If not, write to
# the Free Software Foundation, Inc., 51 Franklin Street,
# Boston, MA 02110-1301, USA.
#

import numpy as np
from gnuradio import gr

class sinad_ff(gr.sync_block):
    """
    docstring for block sinad_ff
    """
    def __init__(self, sinadFreq, Fs):
        gr.sync_block.__init__(self,
            "sinad_ff",
            [np.float32],
            [np.float32])
        self.Fs = Fs
        self.fRef = sinadFreq
        self.refWidth = 20


    def work(self, input_items, output_items):
        output_items[0][:] = self.__calc_sinad(input_items[0])
        return len(output_items[0])

    def __calc_sinad(self, data):
        """ Takes float array and returns the sinad in dB
        """
#%% compute FFT

        # 5/9/07 - added data windowing - ASP
        window = np.hamming( np.size(data) )
        for n in range( np.size(data) ):
            data[n] = data[n] * window[n]

        psd = np.fft.fft(data)
        psd = psd.flatten()

#%% determine bin indices
        bin1 = int(np.floor(float((self.fRef-self.refWidth/2))/float(self.Fs) * psd.shape[0])) - 1
        bin2 = int(np.ceil(float((self.fRef+self.refWidth/2))/float(self.Fs) * psd.shape[0])) - 1

        # 4/9/07 - added filtering 300 - 3000Hz ASP

        bin300hz = int(np.floor(float(300)/float(self.Fs) * psd.shape[0])) - 1
        bin3000hz = int(np.floor(float(3000)/float(self.Fs) * psd.shape[0])) - 1

#%% calculate SINAD = 10*log10(Ps/Pn)
        # 4/9/07 change signal = psd[bin1:bin2] to psd[bin300hz:bin3000hz] - ASP
        signal = psd[bin300hz:bin3000hz]
        noise = np.concatenate((psd[bin300hz:bin1],psd[(bin2+1):bin3000hz]))

        #Ps1 = float(sum(signal.real*signal.real + signal.imag*signal.imag)) #15/8 DON
        #Pn1 = float(sum(noise.real*noise.real + noise.imag*noise.imag))     #15/8 DON

        Ps = np.sum(np.abs(signal)**2)
        Pn = np.sum(np.abs(noise)**2)

        sinad =    10*np.log10(Ps/Pn)

        # if the tone is not present, sinad will be < 0
        if sinad < 0:
            sinad = 0

        return sinad

