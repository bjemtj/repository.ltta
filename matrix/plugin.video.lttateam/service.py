#!/usr/bin/python
# -*- coding: utf-8 -*-

import xbmc
import xbmcaddon
import json
import threading
import time
import socket
from resources.lib import client as mqtt

# from clouddrive.common.ui.logger import Logger

import requests

__addon__ = {}
__addonname__ = ""
__version__ = ""
__icon__ = ""
 


def getSetting(setting):
    global __addon__
    return __addon__.getSetting(setting).strip()

#
# Load before getSession
#
def load_settings():
    global mqttprogress, mqttinterval, mqttdetails, mqttignore, topic, fsappname, fsappkey, fsemail, fspassword,__addon__,__addonname__, __version__,__icon__
    
    __addon__ = xbmcaddon.Addon()
    __addonname__ = __addon__.getAddonInfo('name')
    __version__ = __addon__.getAddonInfo('version')
    __icon__ = __addon__.getAddonInfo('icon')
    
    mqttprogress = getSetting('mqttprogress').lower() == "true"
    mqttinterval = int(getSetting('mqttinterval'))
    mqttdetails = getSetting('mqttdetails').lower() == "true"
    mqttignore = getSetting('mqttignore')
    if mqttignore:
        mqttignore = mqttignore.lower().split(',')
        
    fsappname = getSetting("fsname")
    fsappkey = getSetting("fskey")
    fsemail = getSetting("fsemail")
    fspassword = getSetting("fspassword")
    
    topic = getSetting("mqtttopic")
    if not topic.endswith("/"):
        topic += "/"
    topic += fsappname + "/"
    

activeplayerid = -1
activeplayertype = ""
playbackstate = 0
lasttitle = ""
lastdetail = {}

#
# Returns true when no words are found, false on one or more matches
#


def ignorelist(data, val):
    if val == "filepath":
        val = xbmc.Player().getPlayingFile()
    return all(val.lower().find(v.strip()) <= -1 for v in data)


def mqttlogging(log):
    global __addon__
    if __addon__.getSetting("mqttdebug") == 'true':
        xbmc.log(log)


def sendrpc(method, params):
    res = xbmc.executeJSONRPC(json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}))
    mqttlogging("MQTT: JSON-RPC call "+method+" returned "+res)
    return json.loads(res)

#
# Publishes a MQTT message. The topic is built from the configured
# topic prefix and the suffix. The message itself is JSON encoded,
# with the "val" field set, and possibly more fields merged in.
#


def publish(suffix, val, more):
    global topic, mqc
    robj = {}
    robj["val"] = val
    if more is not None:
        robj.update(more)
    jsonstr = json.dumps(robj)
    fulltopic = topic+"status/"+suffix
    mqttlogging("MQTT: Publishing @"+fulltopic+": "+jsonstr)
    mqc.publish(fulltopic, jsonstr, qos=0, retain=True)

#
# Set and publishes the playback state. Publishes more info if
# the state is "playing"
#


def setplaystate(state, detail):
    global activeplayerid, activeplayertype, playbackstate
    playbackstate = state
    if state == 1:
        res = sendrpc("Player.GetActivePlayers", {})
        activeplayerid = res["result"][0]["playerid"]
        activeplayertype = res["result"][0]["type"]
        if mqttdetails and ignorelist(mqttignore, "filepath"):
            res = sendrpc("Player.GetProperties", {"playerid": activeplayerid, "properties": [
                          "speed", "currentsubtitle", "currentaudiostream", "repeat", "subtitleenabled"]})
            publish("playbackstate", state, {
                    "kodi_state": detail, "kodi_playbackdetails": res["result"], "kodi_playerid": activeplayerid, "kodi_playertype": activeplayertype, "kodi_timestamp": int(time.time())})
            publishdetails()
        else:
            publish("playbackstate", state, {"kodi_state": detail, "kodi_playerid": activeplayerid,
                    "kodi_playertype": activeplayertype, "kodi_timestamp": int(time.time())})
    else:
        publish("playbackstate", state, {"kodi_state": detail, "kodi_playerid": activeplayerid,
                "kodi_playertype": activeplayertype, "kodi_timestamp": int(time.time())})


def convtime(ts):
    return("%02d:%02d:%02d" % (ts/3600, (ts/60) % 60, ts % 60))

#
# Publishes playback progress
#


def publishprogress():
    global player
    if not player.isPlaying():
        return
    pt = player.getTime()
    tt = player.getTotalTime()
    if pt < 0:
        pt = 0
    if tt > 0:
        progress = (pt*100)/tt
    else:
        progress = 0
    state = {"kodi_time": convtime(pt), "kodi_totaltime": convtime(tt)}
    publish("progress", "%.1f" % progress, state)

#
# Publish more details about the currently playing item
#


def publishdetails():
    global player, activeplayerid
    global lasttitle, lastdetail
    if not player.isPlaying():
        return
    if ignorelist(mqttignore, "filepath"):
        res = sendrpc("Player.GetItem", {"playerid": activeplayerid, "properties": [
                      "title", "streamdetails", "file", "thumbnail", "fanart"]})
        if "result" in res:
            newtitle = res["result"]["item"]["title"]
            newdetail = {"kodi_details": res["result"]["item"]}
            if newtitle != lasttitle or newdetail != lastdetail:
                lasttitle = newtitle
                lastdetail = newdetail
                if ignorelist(mqttignore, newtitle):
                    publish("title", newtitle, newdetail)
    if mqttprogress:
        publishprogress()

#
# Notification subclasses
#


class MQTTMonitor(xbmc.Monitor):
    def onSettingsChanged(self):
        global mqc, player, monitor, mqttinterval
        send_notify("Settings changed, reconnecting")
        mqttlogging("MQTT: Settings changed, reconnecting broker")
        try:
            mqc.loop_stop(True)
        except NameError:
            mqttlogging("MQTT: mqc is not defined")
        
        load_settings()
        if fsGetSession():
            if startmqtt():
                send_notify("Ready")
                player = MQTTPlayer()
                if mqttprogress:
                    mqttlogging(
                        "MQTT: Progress Publishing enabled, interval is set to %d seconds" % mqttinterval)
                    while not monitor.waitForAbort(mqttinterval):
                        publishprogress()
                else:
                    mqttlogging(
                        "MQTT: Progress Publishing disabled, waiting for abort")
                    monitor.waitForAbort()
                mqc.loop_stop(True)
            else:
                send_notify("Could not connect to server")
        else:
            send_notify("Please setup settings")


class MQTTPlayer(xbmc.Player):

    def onPlayBackStarted(self):
        setplaystate(1, "started")

    def onPlayBackPaused(self):
        setplaystate(2, "paused")

    def onPlayBackResumed(self):
        setplaystate(1, "resumed")

    def onPlayBackEnded(self):
        setplaystate(0, "ended")

    def onPlayBackStopped(self):
        setplaystate(0, "stopped")

    def onPlayBackSeek(self):
        publishprogress()

    def onPlayBackSeek(self):
        publishprogress()

    def onPlayBackSeekChapter(self):
        publishprogress()

    def onPlayBackSpeedChanged(speed):
        setplaystate(1, "speed")

    def onQueueNextItem():
        mqttlogging("MQTT onqn")

#
# Handles commands
#


def processnotify(data):
    try:
        params = json.loads(data)
    except ValueError:
        parts = data.split(None, 1)
        params = {"title": parts[0], "message": parts[1]}
    sendrpc("GUI.ShowNotification", params)


def processplay(data):
    global player
    try:
        params = json.loads(data)
        sendrpc("Player.Open", params)
    except ValueError:
        try:
            player.play(data)
        except NameError:
            player = MQTTPlayer()
            player.play(data)


def processplaybackstate(data):
    global playbackstate, player
    if data == "0" or data == "stop":
        player.stop()
    elif data == "1" or data == "resume" or data == "play":
        if playbackstate == 2:
            player.pause()
        elif playbackstate != 1:
            player.play()
    elif data == "2" or data == "pause":
        if playbackstate == 1:
            player.pause()
    elif data == "toggle":
        if playbackstate == 1 or playbackstate == 2:
            player.pause()
    elif data == "next":
        player.playnext()
    elif data == "previous":
        player.playprevious()


def processcommand(topic, data):
    if topic == "notify":
        processnotify(data)
    elif topic == "play":
        fsGetLink(data)
        processplay(fsPlayLink)
    elif topic == "playbackstate":
        processplaybackstate(data)
    else:
        mqttlogging("MQTT: Unknown command "+topic)

#
# Handles incoming MQTT messages
#


def msghandler(mqc, userdata, msg):
    mqttlogging("MQTT: Receive command %s: %s" % (msg.topic,msg.payload.decode("utf-8")))
    try:
        global topic
        if msg.retain:
            return
        mytopic = msg.topic[len(topic):]
        commandSub = "command/"
        if mytopic.startswith(commandSub):
            mqttlogging("MQTT: processcommand %s: %s" % (mytopic[len(commandSub):], msg.payload.decode("utf-8")))
            processcommand(mytopic[len(commandSub):], msg.payload.decode("utf-8"))
    except Exception as e:
        mqttlogging("MQTT: Error processing message %s: %s" %
                    (type(e).__name__, e))


def connecthandler(mqc, userdata, rc):
    mqttlogging("MQTT: Connected to MQTT broker with rc=%d" % (rc))
    mqc.subscribe(topic+"command/#", qos=0)
    mqttlogging("MQTT: subscribe to '"+topic+"command/#' with qos=0")


def disconnecthandler(mqc, userdata, rc):
    mqttlogging("MQTT: Disconnected from MQTT broker with rc=%d" % (rc))
    time.sleep(5)
    mqc.reconnect()
    mqttlogging("MQTT: Reconnect to MQTT broker with rc=%d" % (rc))

#
# Starts connection to the MQTT broker, sets the will
# and subscribes to the command topic
#


def startmqtt():
    global topic, mqc, __addon__
    mqc = mqtt.Client()
    mqc.on_message = msghandler
    mqc.on_connect = connecthandler
    mqc.on_disconnect = disconnecthandler
    if __addon__.getSetting("mqttanonymousconnection") == 'false':
        mqc.username_pw_set(__addon__.getSetting(
            "mqttusername"), __addon__.getSetting("mqttpassword"))
        mqttlogging("MQTT: Anonymous disabled, connecting as user: %s" %
                    __addon__.getSetting("mqttusername"))
    if __addon__.getSetting("mqtttlsconnection") == 'true' and __addon__.getSetting("mqtttlsconnectioncrt") != '' and __addon__.getSetting("mqtttlsclient") == 'false':
        mqc.tls_set(__addon__.getSetting("mqtttlsconnectioncrt"))
        mqttlogging("MQTT: TLS enabled, connecting using CA certificate: %s" %
                    __addon__.getSetting("mqtttlsconnectioncrt"))
    elif __addon__.getSetting("mqtttlsconnection") == 'true' and __addon__.getSetting("mqtttlsclient") == 'true' and __addon__.getSetting("mqtttlsclientcrt") != '' and __addon__.getSetting("mqtttlsclientkey") != '':
        mqc.tls_set(__addon__.getSetting("mqtttlsconnectioncrt"), __addon__.getSetting(
            "mqtttlsclientcrt"), __addon__.getSetting("mqtttlsclientkey"))
        mqttlogging("MQTT: TLS with client certificates enabled, connecting using certificates CA: %s, client %s and key: %s" % (
            __addon__.getSetting("mqttusername"), __addon__.getSetting("mqtttlsclientcrt"), __addon__.getSetting("mqtttlsclientkey")))
    
    mqc.will_set(topic+"connected", 0, qos=2, retain=True)
    sleep = 2
    for attempt in range(10):
        try:
            mqttlogging("MQTT: Connecting to MQTT broker at %s:%s" % (
                __addon__.getSetting("mqtthost"), __addon__.getSetting("mqttport")))
            mqc.connect(__addon__.getSetting("mqtthost"),
                        __addon__.getSetting("mqttport"), 60)
        except socket.error:
            mqttlogging("MQTT: Socket error raised, retry in %d seconds" % sleep)
            monitor.waitForAbort(sleep)
            sleep = sleep*2
        else:
            break
    else:
        mqttlogging("MQTT: No connection possible, giving up")
        return(False)
    mqc.publish(topic+"connected", 2, qos=1, retain=True)
    mqc.loop_start()
    return(True)
#
# Send notify
#
def send_notify(msg):
    global __addonname__,__icon__
    time = 5000 #in miliseconds
    
    xbmc.executebuiltin('Notification(%s, %s, %d, %s)'%(__addonname__,msg, time, __icon__))


#
# Fshare login
#
fsSession= {}
fsappname = ""
fsappkey = ""
fsemail = ""
fspassword = ""
topic = ""
def fsGetSession():
    global fsSession, topic, fsappname, fsappkey, fsemail, fspassword
    
    
    service_url = "https://api.fshare.vn/api/user/login"
    headers = {
        "cache-control": "no-cache",
        "Content-Type": "application/json",
        "User-Agent": fsappname
    }
    payload = {
        "app_key": fsappkey,
        "user_email": fsemail,
        "password": fspassword
    }
    mqttlogging("FS: payload: %s" % json.dumps(payload))
    if fspassword == "defaultpassword":
        mqttlogging("FS: return caused default settings")
        return(False)
    try:
        response = requests.post(
            service_url, data=json.dumps(payload), headers=headers
        )
        fsSession = response.json()
        if response.status_code == 200:
            mqttlogging("FS: msg: %s" % fsSession["msg"])
            return(True)
        elif response.status_code == 403 or response.status_code == 405:
            send_notify(fsSession["msg"])
        else:
            mqttlogging("FS: Error in get session %s" % response.content)
        return(False)
    except Exception as e:
        mqttlogging("FS: Error in get session %s: %s" %
                    (type(e).__name__, e))
        mqttlogging("FS: Error in get session %s" % response.content)
        return(False)
#
# Fshare get link
#      
fsFileLink = ""
fsPlayLink = ""
def fsGetLink(url):
    global fsSession, fsFileLink, fsPlayLink, fsappname
    
    fsappname = getSetting("fsname")
    
    service_url = "https://api.fshare.vn/api/session/download"
    headers = {
        "Authorization":"Bearer "+fsSession["token"],
        "Cookie":"session_id="+fsSession["session_id"],
        "cache-control": "no-cache",
        "Content-Type": "application/json",
        "User-Agent": fsappname
    }
    payload = {
        "zipflag" : 0,
        "url" : url,
        "password" : "",
        "token": fsSession["token"]
    }
    try:
        response = requests.post(
            service_url, data=json.dumps(payload), headers=headers
        )
        mqttlogging("FS: Response get link %s: %s" % (response.status_code,json.dumps(response.json())))

        if response.status_code == 200:
            fsPlayLink = response.json()["location"]
            mqttlogging("FS: Got fsPlayLink: %s" % fsPlayLink)
            return(True)
        return(False)
    except Exception as e:
        mqttlogging("FS: Error in get link %s: %s" %
                    (type(e).__name__, e))
        mqttlogging("FS: Error in get link %s" % response.content)
        return(False)
    
#
# Addon initialization and shutdown
#
if (__name__ == "__main__"):
    global monitor, player
    load_settings()
    mqttlogging('MQTT: MQTT Adapter Version %s started' % __version__)
    monitor = MQTTMonitor()
    if fsGetSession():
        if startmqtt():
            send_notify("Ready")
            player = MQTTPlayer()
            if mqttprogress:
                mqttlogging(
                    "MQTT: Progress Publishing enabled, interval is set to %d seconds" % mqttinterval)
                while not monitor.waitForAbort(mqttinterval):
                    publishprogress()
            else:
                mqttlogging(
                    "MQTT: Progress Publishing disabled, waiting for abort")
                monitor.waitForAbort()
            mqc.loop_stop(True)
        mqttlogging("MQTT: Shutting down")
    else:
        mqttlogging("FS: Get session failed")
        send_notify("Please setup settings")
        monitor.waitForAbort()
