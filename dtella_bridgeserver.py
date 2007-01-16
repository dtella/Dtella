#!/usr/bin/env python
#
#IRC Bridge Test Code
#NOTE: This is only designed to work with UnrealIRCd
#

import fixtwistedtime

from twisted.internet.protocol import ClientFactory, ServerFactory
from twisted.protocols.basic import LineOnlyReceiver
from twisted.internet import reactor
from twisted.python.runtime import seconds

from Crypto.Util.number import long_to_bytes, bytes_to_long
from Crypto.PublicKey import RSA

import time
import struct
import md5

import dtella
import dtella_state
import dtella_crypto
import dtella_local
from dtella_util import Ad, dcall_discard, dcall_timeleft


import dtella_bridge_config as cfg


def irc_enc_nick(nick):
    return cfg.irc_prefix + nick

def irc_dec_nick(nick):
    if len(nick) > 1 and nick[0] == cfg.irc_prefix:
        return nick[1:]
    else:
        raise ValueError

def dc_enc_nick(nick):
    return cfg.dc_prefix + nick

def dc_dec_nick(nick):
    if len(nick) > 1 and nick[0] == cfg.dc_prefix:
        return nick[1:]
    else:
        raise ValueError


class IRCBadMessage(Exception):
    pass


class IRCServer(LineOnlyReceiver):
    showirc = True

    def __init__(self, main):
        self.data = IRCServerData(self)
        self.main = main
        self.syncd = False
        self.readytosend = False


    def connectionMade(self):
        self.main.ircs = self
        self.sendLine("PASS :%s" % (cfg.irc_password,))
        self.sendLine("SERVER %s 1 :%s" % (cfg.my_host, cfg.my_name))


    def sendLine(self, line):
        line = line.replace('\r', '').replace('\n', '')
        print "<:", line
        LineOnlyReceiver.sendLine(self, line)


    def lineReceived(self, line):

        # :darkhorse KILL }darkhorse :dhirc.com!darkhorse (TEST!!!)


        osm = self.main.osm
        
        prefix, command, args = self.parsemsg(line)
        if self.showirc:
            pass
            #print ">:", repr(prefix), repr(command), repr(args)
            print ">:", line

        if command == "PING":
            print "PING? PONG!"
            self.sendLine("PONG :%s" % (args[0]))

        elif command == "NICK":

            if args[0][:1] == cfg.irc_prefix:
                self.sendLine(":%s KILL %s :%s (nick reserved for Dtella)" %
                              (cfg.my_host, args[0], cfg.my_host))
                return
                
            if prefix:
                self.data.changeNick(prefix, args[0])
            else:
                self.data.addNick(args[0])

        elif command == "JOIN":
            chans = args[0].split(',')
            self.data.gotJoin(prefix, chans)

        elif command == "PART":
            chans = args[0].split(',')
            self.data.gotPart(prefix, chans)

        elif command == "QUIT":
            self.data.gotPart(prefix, None)

        elif command == "KICK":
            chan = args[0]
            l33t = prefix
            n00b = args[1]
            reason = args[2]
            
            self.data.gotKick(chan, l33t, n00b, reason)

            

        elif command == "SERVER":
            # If we receive this, our password was accepted, so broadcast
            # the Dtella state information if it's available and we haven't
            # sent it already.

            self.sendLine(":%s TKL + Q * %s* bridge.dtella.net 0 %d :Reserved for Dtella"
                          % (cfg.my_host, cfg.irc_prefix, time.time()))

            if not self.readytosend:
                self.readytosend = True

                # Send my own bridge nick
                self.pushFullJoin(cfg.bot_irc, "bridge", "dtella.net")

                # Maybe send Dtella nicks
                if self.main.osm and self.main.osm.syncd:
                    self.sendState()
            

        elif command == "EOS" and prefix == cfg.irc_server:
            print "SYNCD!!!!"

            self.showirc = True

            # If we enter the syncd state, send status to Dtella, if Dtella
            # is ready.  Otherwise, Dtella will send its own state when it
            # becomes ready.

            if not self.syncd:
                self.syncd = True
                
                osm = self.main.osm
                if osm and osm.syncd:
                    osm.bsm.sendState()


        elif command == "PRIVMSG":
            osm = self.main.osm
            if (self.syncd and osm and osm.syncd):

                target = args[0]
                text = args[1]
                flags = 0
                
                if (text[:8], text[-1:]) == ('\001ACTION ', '\001'):
                    text = text[8:-1]
                    flags |= dtella.SLASHME_BIT

                if target == cfg.irc_chan:
                    chunks = []
                    osm.bsm.addChatChunk(
                        chunks, dc_enc_nick(prefix), text, flags)
                    osm.bsm.sendBridgeChange(chunks)

                else:
                    try:
                        nick = irc_dec_nick(target)
                        n = osm.nkm.lookupNick(nick)
                    except (ValueError, KeyError):
                        return

                    chunks = []
                    osm.bsm.addMessageChunk(
                        chunks, dc_enc_nick(prefix), text, flags)
                    osm.bsm.sendPrivateBridgeChange(n, chunks)


        elif command == "NOTICE":
            osm = self.main.osm
            if (self.syncd and osm and osm.syncd):

                target = args[0]
                text = args[1]
                flags = dtella.NOTICE_BIT

                if target == cfg.irc_chan:
                    chunks = []
                    osm.bsm.addChatChunk(
                        chunks, dc_enc_nick(prefix), text, flags)
                    osm.bsm.sendBridgeChange(chunks)

                else:
                    try:
                        nick = irc_dec_nick(target)
                        n = osm.nkm.lookupNick(nick)
                    except (ValueError, KeyError):
                        return

                    chunks = []
                    osm.bsm.addMessageChunk(
                        chunks, dc_enc_nick(prefix), text, flags)
                    osm.bsm.sendPrivateBridgeChange(n, chunks)
                

    def parsemsg(self, s):
        #this breaks up messages received from the other server into their three components:
        # prefix, command, and args
        prefix = ''
        trailing = []
        if not s:
            raise IRCBadMessage("Empty line.")
        if s[0] == ':':
            prefix, s = s[1:].split(' ', 1)
        if s.find(' :') != -1:
            s, trailing = s.split(' :', 1)
            args = s.split()
            args.append(trailing)
        else:
            args = s.split()
        command = args.pop(0)
        return prefix, command, args


    def sendState(self):
        osm = self.main.osm
        assert (self.readytosend and osm and osm.syncd)

        print "Sending Dtella state to IRC..."

        nicks = [(n.nick, n.ipp) for n in osm.nkm.nickmap.values()]
        nicks.sort()

        for nick,ipp in nicks:
            nick = irc_enc_nick(nick)
            host = Ad().setRawIPPort(ipp).getTextIP()
            self.pushFullJoin(nick, "dtnode", host)
                

    def pushFullJoin(self, nick, user, host):
        self.sendLine("NICK %s 0 %d %s %s %s 1 :Dtella Peer" %
            (nick, time.time(), user, host, cfg.my_host))
        self.sendLine(":%s JOIN %s" % (nick, cfg.irc_chan))
        

    def pushQuit(self, nick, reason=""):
        self.sendLine(":%s QUIT :%s" % (nick, reason))
        

    def pushPrivMsg(self, nick, text, target=None, action=False):
        if target is None:
            target = cfg.irc_chan
            
        if action:
            text = "\001ACTION %s\001" % text
        
        self.sendLine(":%s PRIVMSG %s :%s" % (nick, target, text))
    

    def pushNotice(self, nick, text, target=None):
        if target is None:
            target = cfg.irc_chan
        self.sendLine(":%s NOTICE %s :%s" % (nick, target, text))


    def event_AddNick(self, nick, ipp):
        nick = irc_enc_nick(nick)
        host = Ad().setRawIPPort(ipp).getTextIP()
        self.pushFullJoin(nick, "dtnode", host)
        


    def event_RemoveNick(self, nick, reason):
        inick = irc_enc_nick(nick)
        self.pushQuit(inick, reason)


    def event_UpdateInfo(self, nick, info):
        pass


    def event_ChatMessage(self, nick, text, flags):
        inick = irc_enc_nick(nick)

        if flags & dtella.NOTICE_BIT:
            self.pushNotice(inick, text)
        elif flags & dtella.SLASHME_BIT:
            self.pushPrivMsg(inick, text, action=True)
        else:
            self.pushPrivMsg(inick, text)
        
        
##############################################################################


class IRCServerData(object):
    # All users on the IRC network

    class User(object):
        def __init__(self, nick):
            self.nick = nick
            self.chans = set()

    class Channel(object):
        def __init__(self, chan):
            self.chan = chan
            self.users = set()
    

    def __init__(self, ircs):
        self.ulist = {}
        self.clist = {}
        self.ircs = ircs


    def addNick(self, nick):
        self.ulist[nick] = self.User(nick)


    def changeNick(self, oldnick, newnick):
        try:
            u = self.ulist.pop(oldnick)
        except KeyError:
            print "Nick doesn't exist"
            return

        if newnick in self.ulist:
            print "New nick already exists!"
            return

        u.nick = newnick
        self.ulist[newnick] = u

        for chan in u.chans:
            c = self.getChan(chan)
            c.users.discard(oldnick)
            c.users.add(newnick)

        if cfg.irc_chan in u.chans:
            osm = self.ircs.main.osm
            if (self.ircs.syncd and osm and osm.syncd):
                chunks = []
                osm.bsm.addChatChunk(
                    chunks, cfg.bot_nick,
                    "%s is now known as %s" % (dc_enc_nick(oldnick),
                                               dc_enc_nick(newnick))
                    )
                osm.bsm.addNickChunk(chunks, dc_enc_nick(oldnick), 0)
                osm.bsm.addNickChunk(chunks, dc_enc_nick(newnick), 1)
                osm.bsm.sendBridgeChange(chunks)


    def gotKick(self, chan, l33t, n00b, reason):

        if chan == cfg.irc_chan:
            osm = self.ircs.main.osm
            if (self.ircs.syncd and osm and osm.syncd):

                try:
                    nick = irc_dec_nick(n00b)
                    n = osm.nkm.lookupNick(nick)
                except (ValueError, KeyError):
                    # IRC nick
                    chunks = []
                    osm.bsm.addChatChunk(
                        chunks, cfg.bot_nick,
                        "%s has kicked %s: %s" %
                        (dc_enc_nick(l33t), dc_enc_nick(n00b), reason)
                        )
                    osm.bsm.addNickChunk(chunks, dc_enc_nick(n00b), 0)
                    osm.bsm.sendBridgeChange(chunks)
                    
                else:
                    # DC Nick
                    chunks = []
                    osm.bsm.addKickChunk(
                        chunks, n, dc_enc_nick(l33t), reason
                        )
                    osm.bsm.sendBridgeChange(chunks)

                    # Forget this nick
                    osm.nkm.removeNode(n)
                    n.nick = n.info = ''

        try:
            u = self.ulist[n00b]
        except KeyError:
            print "Nick doesn't exist"
            return

        c = self.getChan(chan)
        c.users.discard(n00b)
        u.chans.discard(chan)


    def getChan(self, chan):
        try:
            c = self.clist[chan]
        except KeyError:
            c = self.clist[chan] = self.Channel(chan)
        return c


    def gotJoin(self, nick, chans):
        try:
            u = self.ulist[nick]
        except KeyError:
            print "nick %s doesn't exist!" % (nick,)
            return

        chans = set(chans) - u.chans

        for chan in chans:
            c = self.getChan(chan)
            c.users.add(nick)
            u.chans.add(chan)

        if cfg.irc_chan in chans:
            osm = self.ircs.main.osm
            if (self.ircs.syncd and osm and osm.syncd):
                chunks = []
                osm.bsm.addNickChunk(chunks, dc_enc_nick(nick), 1)
                osm.bsm.sendBridgeChange(chunks)


    def gotPart(self, nick, chans):
        try:
            u = self.ulist[nick]
        except KeyError:
            print "nick %s doesn't exist!" % (nick,)
            return None

        if chans is None:
            # (QUIT)
            del self.ulist[nick]
            chans = u.chans.copy()

        for chan in chans:
            c = self.getChan(chan)
            c.users.discard(nick)
            u.chans.discard(chan)

        if cfg.irc_chan in chans:
            osm = self.ircs.main.osm
            if (self.ircs.syncd and osm and osm.syncd):
                chunks = []
                osm.bsm.addNickChunk(chunks, dc_enc_nick(nick), 0)
                osm.bsm.sendBridgeChange(chunks)

        return chans


    def getNicksInChan(self, chan):
        nicks = list(self.getChan(chan).users)
        nicks.sort()
        return nicks


class IRCFactory(ClientFactory):
    def __init__(self, main):
        self.main = main

    def buildProtocol(self, addr):
        p = IRCServer(self.main)
        p.factory = self
        return p


##############################################################################


class BridgeServerProtocol(dtella.PeerHandler):

    def handlePacket_bP(self, ad, data):
        # Private message to IRC nick

        (kind, src_ipp, ack_key, src_nhash, rest
         ) = self.decodePacket('!2s6s8s4s+', data)

        self.checkSource(src_ipp, ad)

        (dst_nick, rest
         ) = self.decodeString1(rest)

        (flags, rest
         ) = self.decodePacket('!B+', rest)

        (text, rest
         ) = self.decodeString2(rest)

        if rest:
            raise BadPacketError("Extra data")

        osm = self.main.osm
        if not (osm and osm.syncd):
            raise BadTimingError("Not ready for PM")

        osm.bsm.receivedPrivateMessage(src_ipp, ack_key, src_nhash,
                                       dst_nick, text)


    def handlePacket_bQ(self, ad, data):
        # Requesting a full data block

        (kind, src_ipp, bhash
         ) = self.decodePacket('!2s6s16s', data)

        self.checkSource(src_ipp, ad)

        osm = self.main.osm
        if not (osm and osm.syncd):
            raise BadTimingError("Not ready for Bq")

        osm.bsm.receivedBlockRequest(src_ipp, bhash)


##############################################################################


class BridgeServerManager(object):

    class CachedBlock(object):
        
        def __init__(self, data):
            self.data = data
            self.expire_dcall = None

        def scheduleExpire(self, blks, key):
            if self.expire_dcall:
                self.expire_dcall.reset(60.0)
                return

            def cb(blks, key):
                del blks[key]

            self.expire_dcall = reactor.callLater(60.0, cb, blks, key)


    def __init__(self, main):
        self.main = main
        self.rsa_obj = RSA.construct(cfg.private_key)

        # 64-bit value, stored as [8-bit pad] [32-bit time] [24-bit counter]
        self.bridge_pktnum = 0

        self.sendState_dcall = None

        # Incoming private messages:
        self.msgs = {} # {ack_key -> expire_dcall}

        self.cached_blocks = {}  # hash -> CachedBlock()


    def nextPktNum(self):

        t = long(time.time())

        if self.bridge_pktnum >> 24 == t:
            self.bridge_pktnum += 1
            print "nextPktNum: Incrementing"
        else:
            self.bridge_pktnum = (t << 24L)
            print "nextPktNum: New Time"

        return struct.pack("!Q", self.bridge_pktnum)


    def syncComplete(self):
        # This is called from OnlineStateManager after the Dtella network
        # is fully syncd.
        osm = self.main.osm

        # Splice in some handlers
        osm.yqrm.sendSyncReply = self.sendSyncReply
        osm.makeExitPacket = self.makeExitPacket

        ircs = self.main.ircs

        # If the IRC server is ready to receive our state, then send it.
        if ircs and ircs.readytosend:
            ircs.sendState()

        if ircs and ircs.syncd:
            osm.bsm.sendState()


    def signPacket(self, packet, broadcast):

        import time

        data = ''.join(packet)

        if broadcast:
            body = data[0:2] + data[10:]
        else:
            body = data

        data_hash = md5.new(body).digest()
        
        t = time.time()
        sig, = self.rsa_obj.sign(data_hash, None)
        print "Sign Time=", (time.time() - t)

        packet.append(long_to_bytes(sig))


    def sendSyncReply(self, src_ipp, cont, uncont):
        # This gets spliced into the SyncRequestRoutingManager

        ad = Ad().setRawIPPort(src_ipp)
        osm = self.main.osm

        # Build Packet
        packet = ['bY']

        # My IP:Port
        packet.append(osm.me.ipp)

        # seqnum, expire time, session id, uptime, flags, hashes, pubkey
        block_hashes, blocks = self.getStateData(packet)

        # Contacted Nodes
        packet.append(struct.pack('!B', len(cont)))
        packet.extend(cont)

        # Uncontacted Nodes
        packet.append(struct.pack('!B', len(uncont)))
        packet.extend(uncont)

        # Signature
        self.signPacket(packet, broadcast=False)

        # Send it
        self.main.ph.sendPacket(''.join(packet), ad.getAddrTuple())

        # Keep track of the data for a while,
        # so the node can request it.
        for bhash, data in zip(block_hashes, blocks):
            try:
                b = self.cached_blocks[bhash]
            except KeyError:
                b = self.cached_blocks[bhash] = self.CachedBlock(data)
            b.scheduleExpire(self.cached_blocks, bhash)


    def makeExitPacket(self):
        osm = self.main.osm
        packet = osm.mrm.broadcastHeader('BX', osm.me.ipp)
        packet.append(self.nextPktNum())
        self.signPacket(packet, broadcast=True)
        return ''.join(packet)


    def sendState(self):

        dcall_discard(self, 'sendState_dcall')

        def cb():
            self.sendState_dcall = None

            osm = self.main.osm
            ircs = self.main.ircs

            assert (osm and osm.syncd and ircs and ircs.syncd)

            # Decide when to retransmit next
            when = 60 * 5
            self.sendState_dcall = reactor.callLater(when, cb)

            # Broadcast header
            packet = osm.mrm.broadcastHeader('BS', osm.me.ipp)

            # The meat
            block_hashes, blocks = self.getStateData(packet)

            # Signature
            self.signPacket(packet, broadcast=True)

            # Broadcast status message
            osm.mrm.newMessage(''.join(packet), mystatus=True)

            # Broadcast data blocks
            # This could potentially be a bottleneck for slow connections
            for b in blocks:
                packet = osm.mrm.broadcastHeader('BB', osm.me.ipp)
                packet.append(self.nextPktNum())
                packet.append(struct.pack("!H", len(b)))
                packet.append(b)
                osm.mrm.newMessage(''.join(packet))

        self.sendState_dcall = reactor.callLater(0, cb)


    def getStateData(self, packet):
        # All the state info common between BS and Br packets

        osm = self.main.osm
        ircs = self.main.ircs

        # Sequence number
        packet.append(self.nextPktNum())

        # Expiration time
        when = int(dcall_timeleft(self.sendState_dcall))
        packet.append(struct.pack("!H", when))

        # Session ID, uptime flags
        packet.append(osm.me.sesid)
        packet.append(struct.pack("!I", seconds() - osm.me.uptime))
        packet.append(struct.pack("!B", dtella.PERSIST_BIT))
        
        # Build data string, containing all the online nicks
        data = []
        for nick in ircs.data.getNicksInChan(cfg.irc_chan):
            nick = dc_enc_nick(nick)
            data.append('N')
            data.append('\x01')
            data.append(struct.pack('!B', len(nick)))
            data.append(nick)

        data = ''.join(data)

        # Split data string into 1k blocks
        blocks = []
        for i in range(0, len(data), 1024):
            blocks.append(data[i:i+1024])

        block_hashes = [md5.new(b).digest() for b in blocks]

        # Add the list of block hashes
        packet.append(struct.pack("!B", len(block_hashes)))
        packet.extend(block_hashes)

        # Add the public key
        pubkey = long_to_bytes(self.rsa_obj.n)
        packet.append(struct.pack("!H", len(pubkey)))
        packet.append(pubkey)

        # Return hashes and blocks
        return block_hashes, blocks


    def sendBridgeChange(self, chunks):
        osm = self.main.osm
        ircs = self.main.ircs

        assert (osm and osm.syncd and ircs and ircs.syncd)

        packet = osm.mrm.broadcastHeader('BC', osm.me.ipp)
        packet.append(self.nextPktNum())

        chunks = ''.join(chunks)
        packet.append(struct.pack("!H", len(chunks)))
        packet.append(chunks)

        self.signPacket(packet, broadcast=True)

        osm.mrm.newMessage(''.join(packet))


    def sendPrivateBridgeChange(self, n, chunks):

        osm = self.main.osm
        ircs = self.main.ircs

        assert (osm and osm.syncd and ircs and ircs.syncd)

        chunks = ''.join(chunks)

        ack_key = self.nextPktNum()

        packet = ['bC']
        packet.append(osm.me.ipp)
        packet.append(ack_key)
        packet.append(n.nickHash())
        packet.append(struct.pack('!H', len(chunks)))
        packet.append(chunks)
        self.signPacket(packet, broadcast=False)
        packet = ''.join(packet)

        def fail_cb():
            print "bC failed."

        osm.pmm.sendMessage(n, ack_key, packet, fail_cb)


    def addNickChunk(self, chunks, nick, mode):
        chunks.append('N')
        chunks.append(struct.pack("!BB", mode, len(nick)))
        chunks.append(nick)


    def addKickChunk(self, chunks, n, l33t, reason):

        # Pick a packet number that's a little bit ahead of what the node
        # is using, so that any status messages sent out by the node at
        # the same time will be overriden by the kick.
        
        n.status_pktnum = (n.status_pktnum + 3) % 0x100000000

        flags = 0

        chunks.append('K')
        chunks.append(n.ipp)
        chunks.append(struct.pack("!IBB", n.status_pktnum, flags, len(l33t)))
        chunks.append(l33t)
        chunks.append(struct.pack("!H", len(reason)))
        chunks.append(reason)


    def addChatChunk(self, chunks, nick, text, flags=0):

        chat_pktnum = self.main.osm.mrm.getPacketNumber_chat()

        chunks.append('C')
        chunks.append(struct.pack('!I', chat_pktnum))
        chunks.append(struct.pack('!BB', flags, len(nick)))
        chunks.append(nick)

        text = text[:512]
        chunks.append(struct.pack('!H', len(text)))
        chunks.append(text)


    def addMessageChunk(self, chunks, nick, text, flags=0):
        chunks.append('M')
        chunks.append(struct.pack('!BB', flags, len(nick)))
        chunks.append(nick)
        
        text = text[:512]
        chunks.append(struct.pack('!H', len(text)))
        chunks.append(text)


    def receivedBlockRequest(self, src_ipp, bhash):
        try:
            b = self.cached_blocks[bhash]
        except KeyError:
            print "Requested block not found"
            return

        b.scheduleExpire(self.cached_blocks, bhash)        

        packet = ['bB']
        packet.append(self.main.osm.me.ipp)
        packet.append(struct.pack('!H', len(b.data)))
        packet.append(b.data)

        ad = Ad().setRawIPPort(src_ipp)
        self.main.ph.sendPacket(''.join(packet), ad.getAddrTuple())


    def receivedPrivateMessage(self, src_ipp, ack_key,
                               src_nhash, dst_nick, text):

        osm = self.main.osm
        ircs = self.main.ircs

        ack_flags = 0

        try:
            # Make sure we're ready to receive it
            if not (osm and osm.syncd and ircs and ircs.readytosend):
                raise dtella.Reject

            try:
                n = osm.lookup_ipp[src_ipp]
            except KeyError:
                # Never heard of this node
                raise dtella.Reject

            if not n.expire_dcall:
                # Node isn't online
                raise dtella.Reject
            
            if src_nhash != n.nickHash():
                # Source nickhash mismatch
                raise dtella.Reject

            if ack_key not in self.msgs:
                # Haven't seen this message before, so handle it

                try:
                    dst_nick = dc_dec_nick(dst_nick)
                except ValueError:
                    raise dtella.Reject
                
                if dst_nick not in ircs.data.ulist:
                    # User isn't on IRC
                    raise dtella.Reject

                ircs.sendLine(":%s PRIVMSG %s :%s" %
                              (irc_enc_nick(n.nick), dst_nick, text))

            # Forget about this message in a minute
            try:
                self.msgs[ack_key].reset(60.0)
            except KeyError:
                def cb():
                    self.msgs.pop(ack_key)
                self.msgs[ack_key] = reactor.callLater(60.0, cb)

        except dtella.Reject:
            ack_flags |= dtella.ACK_REJECT_BIT

        self.main.ph.sendAckPacket(src_ipp, dtella.ACK_PRIVATE,
                                   ack_flags, ack_key)


    def shutdown(self):
        dcall_discard(self, 'sendState_dcall')

        for dcall in self.msgs.itervalues():
            dcall.cancel()

        self.msgs.clear()

        for b in self.cached_blocks.itervalues():
            dcall_discard(b, 'expire_dcall')


##############################################################################


class DtellaBridgeMain(object):
    def __init__(self):

        # IRC Server
        self.ircs = None

        # Initial Connection Manager
        self.icm = None

        # Neighbor Connection Manager
        self.osm = None

        self.reconnect_dcall = None

        # State Manager
        self.state = dtella_state.StateManager(self, 'dtella_bridge.state')

        # Pakcet Encoder
        self.pk_enc = dtella_crypto.PacketEncoder(dtella_local.network_key)

        # Peer Handler
        self.ph = BridgeServerProtocol(self)

        reactor.listenUDP(cfg.udp_port, self.ph)

        # Register a function that runs before shutting down
        reactor.addSystemEventTrigger('before', 'shutdown',
                                      self.cleanupOnExit)

        ircfactory = IRCFactory(self)
        reactor.connectTCP(cfg.irc_server, cfg.irc_port, ircfactory)

        self.startConnecting()


    def cleanupOnExit(self):
        print "Reactor is shutting down.  Doing cleanup."
        self.shutdown(final=True)
        self.state.saveState()


    def startConnecting(self):
        # If all the conditions are right, start connection procedure

        if self.icm or self.osm:
            raise dtella.WhoopsError("Can't start connecting in this state")

        def cb():
            icm = self.icm
            self.icm = None
            
            if icm.node_ipps:
                self.startNodeSync(icm.node_ipps)
            else:
                if not (icm.stats_bad_ip or icm.stats_dead_port):
                    self.showLoginStatus("No online nodes found.")
                    self.shutdown()

                elif icm.stats_bad_ip >= icm.stats_dead_port:
                    self.shutdown(final=True)
                    self.showLoginStatus("Your IP address is not authorized to use this network.")

                else:
                    self.showLoginStatus("Port not forwarded.")
                    self.shutdown(final=True)

        self.ph.remap_ip = None
        self.icm = dtella.InitialContactManager(self, cb)


    def startNodeSync(self, node_ipps=()):
        # Determine my IP address and enable the osm
        
        if self.icm or self.osm:
            raise dtella.WhoopsError("Can't start syncing in this state")

        ad = Ad().setTextIP(cfg.my_ip)
        ad.port = self.state.udp_port

        my_ipp = ad.getRawIPPort()

        bsm = BridgeServerManager(self)

        # Enable the object that keeps us online
        self.osm = dtella.OnlineStateManager(self, my_ipp, node_ipps, bsm=bsm)


    def showLoginStatus(self, text, counter=None):
        print text

    def disableCopyStatusToPM(self):
        pass

    def enableCopyStatusToPM(self, final_shutdown=False):
        pass


    def shutdown(self, final=False):
        # Do a total shutdown of this Dtella node

        if self.icm or self.osm:
            self.showLoginStatus("Shutting down.")

        # Shut down InitialContactManager
        if self.icm:
            self.icm.shutdown()
            self.icm = None

        # Shut down OnlineStateManager
        if self.osm:
            self.osm.shutdown()
            self.osm = None


    def getOnlineDCH(self):
        # Return DCH, iff it's fully online.
        return None


    def getStateObserver(self):
        # Return the IRC Server, iff it's fully online

        if not (self.osm and self.osm.syncd):
            return None

        if self.ircs and self.ircs.readytosend:
            return self.ircs

        return None
        


    def addMyIPReport(self, from_ad, my_ad):
        return


#def main():
#    ircfactory = IrcFactory()
#    reactor.connectTCP(cfg.irc_server, cfg.irc_port, ircfactory)
#    reactor.run()


if __name__ == '__main__':
    dtMain = DtellaBridgeMain()
    dtMain.state.udp_port = cfg.udp_port

    for addr in cfg.ip_cache:
        dtMain.state.refreshPeer(Ad().setTextIPPort(addr), 0)
    
    reactor.run()


