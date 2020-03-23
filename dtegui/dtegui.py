import sshexpect
import tkinter as tk
import pygubu
import os
import json
import queue
import threading
import time
import sys
import math
from functools import partial
import click

LINUX_PROMPT = "~ # "
DTE_PROMPT = "root@localhost>"
SSH_USERNAME="root"
SSH_PORT=614
POLL_DELAY = .100
DTE_DELAY=.001

@click.command()
@click.argument('shelfip')
@click.argument('slotnum')
@click.argument('slotipv6')
@click.argument('cfg_file')
@click.option('--ssh_echo',default=False)
@click.option('--debug',default=False)

def dteguiCli(shelfip,slotnum,slotipv6,cfg_file,ssh_echo,debug):
    root = tk.Tk()
    dg=dtegui(root,shelfip,slotnum,slotipv6,cfg_file,ssh_echo,debug)
    root.mainloop()
    os._exit(0)

class dtegui:
    def __init__(self, master, shelfIP, slotNum, slotIpv6,cfg_file,ssh_echo=False,debug=False):
        self.builder = builder = pygubu.Builder()
        self.master = master
        with open(cfg_file,'r') as f:
            self.configDict = configDict = json.load(f)
        genDict = configDict['General']
        libpath = cfg_file.rsplit('\\',1)[0]+"\\"
        builder.add_from_file(libpath+genDict['uiFile'])
        print (builder.tkvariables)
        self.mainwindow = builder.get_object(genDict['topFrame'], master)
        master.title(genDict['winTitle'])
        master.iconbitmap(libpath+genDict['icoFile'])
        self.ssh = None
        self.slotNumVar = builder.tkvariables.__getitem__('slotNumVar')
        self.shelfIPVar = builder.tkvariables.__getitem__('shelfIPVar')
        
        self.shelfIP = shelfIP
        self.slotNum = slotNum
        self.slotIpv6= slotIpv6
        self.slotNumVar.set(slotNum)
        self.shelfIPVar.set(shelfIP)

        self.cfg_file=cfg_file
        self.queueThread = None
        self.pollThread = None
        self.initThread = None
        self.cmdq = queue.Queue()
        self.applyDict={}
        self.initList=[]
        self.pollDict={}
        self.vars = vars()
        self.debug=debug
        self.sshEcho=ssh_echo
        self.configGui()
        self.notebook = builder.get_object('Notebook_1')
        self.notebook.bind("<<NotebookTabChanged>>", self.handle_tab_changed)
        self.current_tab = tk.StringVar()

        builder.connect_callbacks(self)
        
        if self.startDTE():
            self.dteAlive = True
            self.startThread(self.queueThread,queueThread(self))
            self.startThread(self.initThread,initThread(self))
            return
            
        master.destroy()
        print("Unable to start DTE!")
        
    def startDTE(self):
    
        self.ssh = ssh = sshexpect.spawn(ipaddress=self.shelfIP,username=SSH_USERNAME,port=SSH_PORT)
        if ssh.closed: return False
        if self.debug:print("SSH session started.")
        ssh.expect(LINUX_PROMPT)
        ssh.sendln("ssh -o StrictHostKeyChecking=no root@"+self.slotIpv6+"%mgmt")
        ssh.expect(LINUX_PROMPT)
        if self.debug:print(ssh.before)
        ssh.sendln("aosCoreDteConsole")
        prompt = ssh.expect([DTE_PROMPT,LINUX_PROMPT])
        if (prompt==1):
            return False
        if self.debug:print(ssh.before)
        ssh.sendln("go debug/aosFwHal/adva.hbmcard.qflex")
        ssh.expect(DTE_PROMPT)
        if self.debug:print(ssh.before)
        if "[/debug/aosFwHal/adva.hbmcard.qflex]" in ssh.before:
            return True
        return False
        
    def event_handler(self,command,cmdType):
    
        varnames = self.configDict["Commands"][command]["Var"]
        for varname in varnames:
            varDict = self.configDict["Variables"][varname]
            if "Group" in varDict:
                groupEnVarName = varDict["Group"][1]
                if self.vars[groupEnVarName].get():
                    value = self.vars[varname].get()
                    groupName = varDict["Group"][0]
                    for item in self.applyDict[groupName]:
                        if item == varname: continue
                        self.vars[item].set(value)
            
        self.cmdq.put(self.elaborateCmd(command,cmdType))
    
    def elaborateCmd(self,command,cmdType):
        parseArgs=[]
        decode=[]
        vars =[]
        cmdDict = self.configDict["Commands"][command]   
        if cmdType in cmdDict:
            cmdStr = cmdDict[cmdType][:]
            if cmdType=="Read":
                parseArgs=[cmdDict["Splitchar"],cmdDict["Trigger"],cmdDict["Location"]]
        elif "Macro" in cmdDict:
            macroDict = self.configDict["Macros"][cmdDict["Macro"]["Name"]]
            cmdStr = macroDict[cmdType][:]
  
            for strReplace in cmdDict["Macro"]:
                if strReplace=="Name":
                    continue
                new = cmdDict['Macro'][strReplace]
                old="<"+strReplace+">"
                if old in cmdStr:
                    cmdStr = cmdStr.replace(old,new)
            if cmdType=="Read":
                parseArgs=[macroDict["Splitchar"],macroDict["Trigger"],macroDict["Location"]]
                
        if self.debug: print("ELAB:",cmdStr,parseArgs)
                
        if cmdType == "Write":
            val = self.vars[cmdDict["Var"][0]].get()
            if "Codec" in cmdDict:
                codecs = cmdDict["Codec"]
                for codec in codecs:
                    codecDict = self.configDict["Codecs"][codec]
                    if "Encode" in codecDict:
                        func=codecDict["Encode"]
                        val=eval(func)
            cmdStr = cmdStr.replace("<Value>",str(val))         

        elif cmdType == "Read" :
            for varname in cmdDict["Var"]:
                vars.append(self.vars[varname])
            if "Codec" in cmdDict:
                codecs = cmdDict["Codec"]
                if self.debug: print(codecs)
                for codec in codecs:
                    codecDict = self.configDict["Codecs"][codec]
                    if "Decode" in codecDict:
                        decode.append(codecDict["Decode"])
                    else:
                        decode.append("")
        if self.debug: print("ELAB:",vars,decode)                
        return (cmdStr,cmdType,parseArgs,decode,vars)
                        
    
    def startThread(self,var,thread):
        if var!=None and var.isAlive()==True:
            return
        self.var = thread
        self.var.start()
        
    def queue_handler(self):
        while (1):
            if not self.cmdq.empty():
                command,type,parse,decoders,vars=self.cmdq.get()
                self.ssh.sendln(command)
                self.ssh.expect(DTE_PROMPT)
                if self.sshEcho: print ("SSH:",self.ssh.before)
                if type == "Read":
                    if self.debug: print(parse,decoders,vars)
                    strvals = self.ssh.parsebefore(split=parse[0],trigger=parse[1],location=parse[2])
                    if self.debug: print("Parsed Value:",strvals)
                    if len (strvals) < len(vars):
                        print ("DTE parse of read command failed!")
                        self.cmdq.task_done()
                        continue
                    for idx,var in enumerate(vars):
                        val=strvals[idx]
                        if len(decoders)>0:
                            decoder = decoders[idx]
                            if self.debug: print("Decoder:", decoder)
                            retval = eval(decoder)
                        else:
                            retval=val[0]
                        var.set(retval)
                        if self.debug: print("New Value: ",retval)
                        if self.debug: print("Var: ",var)
                self.cmdq.task_done()
            time.sleep(DTE_DELAY)
                
    def poll_handler(self):
        while (1):
            if self.vars["polling_enable"].get()==0:
                if self.debug: print("Exiting polling....")
                return
            tabName=self.current_tab.get()
            if tabName in self.pollDict:
                for command in self.pollDict[tabName]:
                    self.cmdq.put(self.elaborateCmd(command,"Read"))
                    time.sleep(POLL_DELAY)
            else:
                time.sleep(.1)
        
    def init_handler(self):
        for command in self.initList:
            self.cmdq.put(self.elaborateCmd(command,"Read")) 

    def on_polling_changed(self):

        pollEnable = (self.vars["polling_enable"].get())
        if pollEnable:
            self.startThread(self.pollThread,pollThread(self))
    
    def handle_tab_changed(self,event):
        selection=event.widget.select()
        self.current_tab.set(event.widget.tab(selection,"text"))
            
    def configGui(self):
        
        for key in self.configDict["Variables"]:
            varDict = self.configDict["Variables"][key]
            objects = varDict["Objects"]
            if not isinstance(objects,list):
                objects = [objects]
            variable = self.makeVar(key,varDict["VarType"],varDict["VarInit"])
            config=[]
            if "Config" in varDict :
                config = varDict["Config"]
                if not isinstance(config,list):
                    config= [config]
            for idx,obj in enumerate(objects):
                builderObj = self.builder.get_object(obj)
                if 'EventCmd' in varDict:
                    cmdList=varDict['EventCmd']
                    builderObj.config(command=partial(self.event_handler,cmdList[0],cmdList[1]))
                if len(config)>0:
                    exec("builderObj.config("+config[idx]+")")
                if self.debug: print (builderObj.config())
            if "Group" in varDict:
                groupName = varDict["Group"][0]
                if not groupName in self.applyDict:
                    self.applyDict[groupName] = [key]
                self.applyDict[groupName].append (key)
                
        for key in self.configDict["Commands"]:
            cmdDict = self.configDict["Commands"][key]
            if not isinstance(cmdDict["Var"],list):
                cmdDict["Var"] = [cmdDict["Var"]]
            if "PollGroup" in cmdDict:
                group = cmdDict["PollGroup"]
                if not group in self.pollDict:
                    self.pollDict[group] = []
                self.pollDict[group].append(key) 
            if "InitGroup" in cmdDict:
                self.initList.append(key)
            if "Codec" in cmdDict:
                if not isinstance(cmdDict["Codec"],list):
                    cmdDict["Codec"]=[cmdDict["Codec"]]
        if self.debug: print(json.dumps(self.pollDict,indent=1))        
        if self.debug: print(json.dumps(self.configDict,indent=1))
           
    def makeVar(self,varName,varType,varInit):
        if varType == "string":
            self.vars[varName] = tk.StringVar()
        elif varType == "integer":
            self.vars[varName] = tk.IntVar()
        elif varType == "double":
            self.vars[varName] = tk.DoubleVar()
        else:
            self.vars[varName] = tk.BooleanVar()
        self.vars[varName].set(varInit)
        return self.vars[varName]

class queueThread (threading.Thread):
    def __init__(self,ad):
        threading.Thread.__init__(self)
        self.ad = ad
    def run(self):
        self.ad.queue_handler()

class pollThread (threading.Thread):
    def __init__(self,ad):
        threading.Thread.__init__(self)
        self.ad = ad
    def run(self):
        self.ad.poll_handler()

class initThread (threading.Thread):
    def __init__(self,ad):
        threading.Thread.__init__(self)
        self.ad = ad
    def run(self):
        self.ad.init_handler()
        
def twos_complement(hexstr,bits):
    value = int(hexstr,16)
    if value & (1 << (bits-1)):
        value -= 1 << bits
    return value

if __name__ == '__main__':
    dteguiCli()