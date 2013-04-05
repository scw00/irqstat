#!/usr/bin/python

DESCRIPTION="""
A better way to watch /proc/interrupts, especially on large NUMA machines with
so many CPUs that /proc/interrupts is wider than the screen.  Press '0'-'9'
for node views, 't' for node totals
"""

import sys
import tty
import termios
from time import sleep
import subprocess
from optparse import OptionParser, OptionGroup
import select
import thread
import time
import threading

keyevent = threading.Event()

def gen_numa():
    cpunodes = {}
    numacores = {}
    f = subprocess.Popen('numactl --hardware | grep cpus', shell=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    errtxt = f.stderr.readline()
    if errtxt:
        print errtxt + '\r\n'
        print "Is numactl installed?\r"
        exit(1)
    for line in f.stdout.readlines():
        c = line.split()
        if c[0] == "node" and c[2] == "cpus:" and len(c) > 3:
            node = c[1]
            numacores[node] = c[3:]
            for core in c[3:]:
                cpunodes[core] = node
    return numacores, cpunodes

# input character, passed between threads
INCHAR = ''

# Get a single character of input, validate
def wait_for_input():
    global INCHAR
    
    acceptable_keys = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', 't']
    while True:
        ch = False
        
        ch = sys.stdin.read(1)
        
        # simple just to exit on any invalid input
        if not ch in acceptable_keys:
            thread.interrupt_main()
        
        # set the new input and notify the main thread
        INCHAR = ch
        keyevent.set()

def filter_found(name, filter_list):
    for filter in filter_list:
        if filter in name:
            return True
    return False

def display_itop(seconds, rowcnt, iterations, sort, totals, dispnode, zero, filters):
    global INCHAR
    
    irqs = {}
    cpunodes = {}
    numacores = {}
    loops = 0
    cntsz = len('NODEXX')
    
    while True:
        # Grab the new display type at a time when nothing is in flux
        if keyevent.isSet():
            keyevent.clear()
            dispnode = INCHAR if INCHAR in numacores.keys() else '-1'
        
        fd = open('/proc/interrupts', 'r')
        header = fd.readline()
        cpus = []
        for name in header.split():
            num = name[3:]
            cpus.append(num)
            
            # Only query the numa information when something is missing.
            # This is effectively the first time and when any disabled CPUs are enabled
            if not num in cpunodes.keys():
                numacores, cpunodes = gen_numa()
        
        for line in fd.readlines():
            vals = line.split()
            irqnum = vals[0].rstrip(':')
            
            # Optionally exclude rows that are not an IRQ number
            if totals is None:
                try:
                    num = int(irqnum)
                except:
                    continue
    
            irq = {}
            irq['cpus'] = map(int, vals[1:len(cpus)+1])
            irq['oldcpus'] = irqs[irqnum]['cpus'] if irqnum in irqs else [0] * len(cpus)
            irq['name'] = ' '.join(vals[len(cpus)+1:])
            irq['oldsum'] = irqs[irqnum]['sum'] if irqnum in irqs else 0
            irq['sum'] = sum(irq['cpus'])
            irq['num'] = irqnum
            
            for node in numacores.keys():
                oldkey = 'oldsum' + node
                key = 'sum' + node
                irq[oldkey] = irqs[irqnum][key] if irqnum in irqs and key in irqs[irqnum] else 0
                irq[key] = 0
            
            for idx, val in enumerate(irq['cpus']):
                key = 'sum' + cpunodes[cpus[idx]]
                irq[key] = irq[key] + val if key in irq else val
                
            # save old
            irqs[irqnum] = irq
        
        def sort_func(x):
            sortnum = -1
            try:
                sortnum = int(sort)
            except:
                None
            
            if sortnum >=0:
                for node in numacores.keys():
                    if sortnum == int(node):
                        return x['sum' + node] - x['oldsum' + node]
            if sort == 't':
                return x['sum'] - x['oldsum']
            
            if sort == 'n':
                return int(x['num'])
        
        rows = sorted(irqs.values(), key=lambda x: sort_func(x), reverse=(sort != 'n'))

        for idx, irq in enumerate(rows):
            if idx == rowcnt:
                break;
            cntsz = max(cntsz, len(str(irq['sum'] - irq['oldsum'])))
        
        print "" + '\r'
        print "IRQs / " + str(seconds) + " second(s)" + '\r'
        fmtstr = ('IRQ# %' + str(cntsz) + 's') % 'TOTAL'
        
        # node view header
        if int(dispnode) >= 0:
            node = 'NODE%s' % dispnode
            fmtstr += (' %' + str(cntsz) + 's ') % node
            for idx, val in enumerate(irq['cpus']):
                if cpunodes[cpus[idx]] == dispnode:
                    cpu = 'CPU%s' % cpus[idx]
                    fmtstr += (' %' + str(cntsz) + 's ') % cpu
        # top view header
        else:
            for node in sorted(numacores.keys()):
                node = 'NODE%s' % node
                fmtstr += (' %' + str(cntsz) + 's ') % node
        
        fmtstr += ' NAME'
        print fmtstr + '\r'
        
        for idx, irq in enumerate(rows):
            if idx == rowcnt:
                break
            
            if len(filters) and not filter_found(irq['name'], filters):
                continue

            total = irq['sum'] - irq['oldsum']
            if zero and not total:
                continue
            
            # IRQ# TOTAL
            fmtstr = ('%4s %' + str(cntsz) + 'd') % (irq['num'], total)
            
            # node view
            if int(dispnode) >= 0:
                oldkey = 'oldsum' + dispnode
                key = 'sum' + dispnode
                fmtstr += (' %' + str(cntsz) + 's ') % str(irq[key] - irq[oldkey])
                for idx, val in enumerate(irq['cpus']):
                    if cpunodes[cpus[idx]] == dispnode:
                        fmtstr += (' %' + str(cntsz) + 's ') % str(irq['cpus'][idx] - irq['oldcpus'][idx])
            
            # top view
            else:
                for node in sorted(numacores.keys()):
                    oldkey = 'oldsum' + node
                    key = 'sum' + node
                    fmtstr += (' %' + str(cntsz) + 's ') % str(irq[key] - irq[oldkey])
            fmtstr += ' ' + irq['name'] 
            print fmtstr + '\r'
        
        # Update field widths after the first iteration.  Data changes significantly
        # between the all-time stats and the interval stats, so this compresses the
        # fields quite a bit.  Updating every iteration is too jumpy.
        if loops == 0:
            cntsz = len('NODEXX')
        
        loops += 1
        if loops == iterations:
            break
        
        sleep(seconds)

def main(args):
    
    parser = OptionParser(description=DESCRIPTION)

    parser.add_option("-i", "--iterations", default='-1',
                      help="iterations to run")
    parser.add_option("-n", "--node", default='-1',
                      help="view a single node")
    parser.add_option("-r", "--rows", default='10',
                      help="rows to display (default 10)")
    parser.add_option("-s", "--sort", default='t',
                      help="column to sort on ('t':total, 'n':IRQ number, '1':node1, etc) (default: 't')")
    parser.add_option("-t", "--time", default='5',
                      help="update interval in seconds")
    parser.add_option("-z", "--zero", action="store_true",
                      help="exclude inactive IRQs")
    parser.add_option("--filter", default="",
                      help="filter IRQs based on name matching comma separated filters")
    parser.add_option("--totals", action="store_true",
                      help="include total rows")
    
    (options, remaining) = parser.parse_args(args)
    
    if options.filter:
        options.filter = options.filter.split(',')
    else:
        options.filter = []
        
    # Set the terminal to unbuffered, to catch a single keypress
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    tty.setraw(sys.stdin.fileno())
    
    # input thread
    thread.start_new_thread(wait_for_input, tuple())
    
    try:
        display_itop(int(options.time), int(options.rows), int(options.iterations), options.sort, options.totals, options.node, options.zero, options.filter)
    except (KeyboardInterrupt, SystemExit):
        None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

if __name__ == "__main__":
    sys.exit(main(sys.argv))