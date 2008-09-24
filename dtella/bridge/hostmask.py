"""
Dtella - UnrealIRCd-compatible Hostmasking Module
Copyright (C) 2008  Dtella Labs (http://www.dtella.org)
Copyright (C) 2008  Paul Marks

$Id$

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
"""

# This is based on Unreal*/src/modules/cloak.c

import array
import dtella.bridge_config as cfg
from hashlib import md5

KEY1, KEY2, KEY3 = cfg.hostmask_keys

def ipstr(ip):
    return '.'.join(["%d" % o for o in ip])

def split_ip(ip):
    return [int(o) for o in ip.split('.')]

def m(s):
    # MD5
    return md5(s).digest()

def d(in_str):
    # Downsample
    a = array.array('B', in_str)
    result = 0
    for i in range(0,16,4):
        result = (result << 8) + (a[i]^a[i+1]^a[i+2]^a[i+3])
    return result

def mask_hostname(host):
    alpha = d(m(m("%s:%s:%s" % (KEY1, host, KEY2)) + KEY3))

    out = "%s-%X" % (cfg.hostmask_prefix, alpha)

    try:
        out += host[host.index('.'):]
    except ValueError:
        pass

    return out

def mask_ipv4(ip):
    ip = split_ip(ip)
    alpha = d(m(m("%s:%s:%s" % (KEY2, ipstr(ip[:4]), KEY3)) + KEY1))
    beta =  d(m(m("%s:%s:%s" % (KEY3, ipstr(ip[:3]), KEY1)) + KEY2))
    gamma = d(m(m("%s:%s:%s" % (KEY1, ipstr(ip[:2]), KEY2)) + KEY3))

    return "%X.%X.%X.IP" % (alpha, beta, gamma)
