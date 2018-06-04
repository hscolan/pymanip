"""

This module defines another kind of session, meant to be used for asynchronous
monitoring, where each variable can be logged with its own timestamp.

"""

import signal
import functools
import time
import sys
import os.path

import sqlite3
from datetime import datetime
import warnings
import numpy as np
import matplotlib.pyplot as plt

import asyncio
import aiohttp
from aiohttp import web
import aiohttp_jinja2
import jinja2

try:
    import PyQt5.QtCore
except ModuleNotFoundError:
    pass

__all__ = ['AsyncSession']


class AsyncSession:

    def __init__(self, session_name, variable_list=None):
        if variable_list is None:
            variable_list = []
        self.conn = sqlite3.connect(session_name + '.db')
        with self.conn as c:
            tables = list(c.execute("SELECT name FROM sqlite_master;"))
            if not tables:
                c.execute("""
                    CREATE TABLE log_names (
                    name TEXT);
                    """)
                c.execute("""
                    CREATE TABLE log (
                    timestamp INT,
                    name TEXT,
                    value REAL);
                    """)
                c.execute("""
                    CREATE TABLE parameters (
                        name TEXT,
                        value REAL);
                    """)

    def __enter__(self):
        return self

    def __exit__(self, type_, value, cb):
        self.conn.close()

    def add_entry(self, **kwargs):
        with self.conn as c:
            cursor = c.cursor()
            cursor.execute('SELECT name FROM log_names;')
            names = set([d[0] for d in cursor.fetchall()])
            for key, val in kwargs.items():
                if key not in names:
                    c.execute('INSERT INTO log_names VALUES (?);',
                              (key,))
                    names.add(key)
                c.execute('INSERT INTO log VALUES (?,?,?);',
                          (datetime.now().timestamp(), key, val))

    def logged_data(self):
        with self.conn as conn:
            c = conn.cursor()
            c.execute('SELECT name FROM log_names;')
            data = c.fetchall()
        names = set([d[0] for d in c.fetchall()])
        result = dict()
        for name in names:
            result[name] = self.__getitem__(name)
        return result

    def logged_last_values(self):
        with self.conn as conn:
            c = conn.cursor()
            c.execute('SELECT name FROM log_names;')
            names = set([d[0] for d in c.fetchall()])
            result = dict()
            for name in names:
                c.execute("""SELECT timestamp, value FROM log
                             WHERE name='{:}'
                             ORDER BY timestamp DESC
                             LIMIT 1;
                          """.format(name))
                result[name] = c.fetchone()
        return result

    def logged_data_fromtimestamp(self, name, timestamp):
        with self.conn as conn:
            c = conn.cursor()
            c.execute("""SELECT timestamp, value FROM log
                         WHERE name='{:}' AND timestamp > {:}
                         ORDER BY timestamp ASC;
                      """.format(name, timestamp))
            data = c.fetchall()
        t = np.array([d[0] for d in data])
        v = np.array([d[1] for d in data])
        return t, v
        
    def save_parameter(self, **kwargs):
        with self.conn as conn:
            c = conn.cursor()
            for key, val in kwargs.items():
                c.execute("""SELECT rowid FROM parameters
                             WHERE name='{:}';
                          """.format(key))
                rowid = c.fetchone()
                if rowid is not None:
                    rowid = rowid[0]
                    c.execute("""
                        REPLACE INTO parameters
                        (rowid, name, value)
                        VALUES (?,?,?);
                        """, (rowid, key, val))
                else:
                    c.execute("""
                        INSERT INTO parameters
                        (name, value)
                        VALUES (?,?);
                        """, (key, val))

    def parameter(self, name):
        with self.conn as conn:
            c = conn.cursor()
            c.execute("""
                      SELECT value FROM parameters 
                      WHERE name='{:}';
                      """.format(name))
            data = c.fetchone()
            if data:
                return data[0]
        return None

    def has_parameter(self, name):
        return self.parameter(name) is not None

    def parameters(self):
        with self.conn as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM parameters;')
            data = c.fetchall()
        return {d[0]: d[1] for d in data}

    def __getitem__(self, key):
        with self.conn as conn:
            c = conn.cursor()
            c.execute("""
                      SELECT timestamp, value FROM log 
                      WHERE name='{:}';
                      """.format(key))
            data = c.fetchall()
        t = np.array([d[0] for d in data])
        v = np.array([d[1] for d in data])
        return t, v

    async def plot(self, varnames, maxvalues=1000, yscale=None):
        if isinstance(varnames, str):
            varnames = (varnames,)
        param_key_window = '_window_' + '_'.join(varnames)
        param_key_figsize = '_figsize_' + '_'.join(varnames)
        last_update = {k: 0 for k in varnames}
        saved_geom = self.parameter(param_key_window)
        if saved_geom:
            saved_geom = eval(saved_geom)
        saved_figsize = self.parameter(param_key_figsize)
        if saved_figsize:
            saved_figsize = eval(saved_figsize)
        plt.ion()
        fig = plt.figure(figsize=saved_figsize)
        mngr = fig.canvas.manager
        if saved_geom:
            mngr.window.setGeometry(saved_geom)
        ax = fig.add_subplot(111)
        initial_timestamps = dict()
        line_objects = dict()
        while self.running:
            data = {k: self.logged_data_fromtimestamp(k, last_update[k])
                    for k in varnames}
            for name, values in data.items():
                ts, vs = values
                if ts.size > 0:
                    if name in initial_timestamps:
                        #print('updating plot')
                        ts0 = initial_timestamps[name]
                        p = line_objects[name]
                        x = np.hstack((p.get_xdata(),(ts-ts0)/3600))
                        y = np.hstack((p.get_ydata(),vs))
                        if x.size > maxvalues:
                            x = x[-maxvalues:]
                            y = y[-maxvalues:]
                        p.set_xdata(x)
                        p.set_ydata(y)
                        xlim = ax.get_xlim()
                        ylim = ax.get_ylim()
                        if xlim[1] < x[-1]:
                            ax.set_xlim((x[0],x[-1]))
                        if ylim[1] < np.max(y) or ylim[0] > np.min(y):
                            ylim = (min((ylim[0],np.min(y))),
                                    max((ylim[1],np.max(y))))
                            ax.set_ylim(ylim)
                    else:
                        #print('initial plot')
                        ts0 = ts[0]
                        initial_timestamps[name] = ts0
                        x = (ts-ts0)/3600
                        y = vs
                        if x.size > maxvalues:
                            x = x[-maxvalues:]
                            y = y[-maxvalues:]
                        p, = ax.plot(x,y, 'o-', label=name)
                        line_objects[name] = p
                        ax.set_xlabel('t [h]')
                        ax.set_xlim((x[0],x[-1]))
                        if yscale:
                            ax.set_yscale(yscale)
                    last_update[name] = ts[-1]
                    ax.legend()
                #with warnings.catch_warnings():
                #    warnings.simplefilter("ignore")
                #    plt.pause(0.0001)
            await asyncio.sleep(1)
        
        # Saving figure positions
        geom = mngr.window.geometry()
        figsize = tuple(fig.get_size_inches())
        self.save_parameter(**{param_key_window: str(geom),
                               param_key_figsize: str(figsize)})

    #def plot(self, name, num=1):
    #    ts, vs = self[name]
    #    plt.figure(num)
    #    plt.clf()
    #    plt.ion()
    #    plt.plot(ts-ts[0], vs, label=name)
    #    plt.legend(loc='upper left')
    #    with warnings.catch_warnings():
    #        warnings.simplefilter("ignore")
    #        plt.pause(0.0001)

    def ask_exit(self, *args, **kwargs):
        self.running = False
        print(' Signal caught... stopping...')

    async def sleep(self, duration, verbose=True):
        start = time.monotonic()
        while self.running and time.monotonic()-start < duration:
            if verbose:
                print("Sleeping for " +\
                      str(-int(time.monotonic()-start-duration)) +\
                      " s" + " "*8, end='\r')
                sys.stdout.flush()
            if verbose:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    plt.pause(0.7)
                await asyncio.sleep(0.3)
            else:
                await asyncio.sleep(1.0)
        if verbose:
            sys.stdout.write("\n")

    async def server_main_page(self, request):
        context = self.logged_last_values()
        response = aiohttp_jinja2.render_template('main.html',
                                                  request,
                                                  context)
        return response

    async def mytask(self, corofunc):
        while self.running:
            await corofunc(self)
            
    def run(self, *tasks):
        loop = asyncio.get_event_loop()

        # signal handling
        self.running = True
        if sys.platform == 'win32':
            # loop.add_signal_handler raises NotImplementedError
            signal.signal(signal.SIGINT, self.ask_exit)
        else:
            for signame in ('SIGINT', 'SIGTERM'):
                loop.add_signal_handler(getattr(signal, signame),
                                        self.ask_exit)

        # web server
        app = web.Application(loop=loop)
        template_dir = os.path.join(os.path.dirname(__file__),
                                    'web')
        aiohttp_jinja2.setup(app,
                             loader=jinja2.FileSystemLoader(template_dir))
        app.router.add_routes([web.get('/', self.server_main_page)])
        webserver = loop.create_server(app.make_handler(), 
                                       host=None, port=6913)

        # if any of the tasks submitted are coroutinefunctions instead of
        # coroutines, then assume they take only one argument (self)
        tasks_final = list()
        for t in tasks:
            if asyncio.iscoroutinefunction(t):
                tasks_final.append(self.mytask(t))
            elif asyncio.iscoroutine(t):
                tasks_final.append(t)
            else:
                raise TypeError('Coroutine or Coroutinefunction is expected')
        loop.run_until_complete(asyncio.gather(webserver, *tasks_final))

        


if __name__ == '__main__':
    with AsyncSession('Essai') as sesn:
        sesn.add_entry(a=1, b=2)
        sesn.save_parameter(c=3)
        sesn.plot('a')
