-- Execute with mpv's own interpreter; replace only the script API under test.
local real = require 'mp'
local utils = require 'mp.utils'
local handlers, callbacks, timers = {}, {}, {}
local values, crops, now, starts = {}, {}, 0, 0
local overlays, observers, timeouts = {}, {}, {}
local bindings, launches = {}, {}
local fake = {msg={error=function() end}}
fake.get_property_native=function(k)
    if k == 'osd-dimensions' then return {w=1280,h=720} end
    return values[k]
end
fake.create_osd_overlay=function(kind)
    assert(kind == 'ass-events')
    local overlay = {updates=0, removed=false}
    function overlay:update() self.updates=self.updates+1; self.removed=false end
    function overlay:remove() self.removed=true end
    overlays[#overlays+1]=overlay
    return overlay
end
fake.observe_property=function(k,_,fn) observers[k]=fn end
fake.get_time=function() return now end
fake.osd_message=function() end
fake.get_script_name=function() return 'reframe' end
fake.get_property=function() return 'existing-pipe' end
fake.set_property=function() error('Existing endpoint must not change') end
fake.set_property_native=function(k,v) values[k]=v; return true end
fake.commandv=function(_, topic, raw) crops[#crops+1]=utils.parse_json(raw) end
fake.register_script_message=function(k,fn) handlers[k]=fn end
fake.register_event=function() end
fake.add_key_binding=function(key,name,fn) bindings[name]={key=key, fn=fn} end
fake.add_timeout=function(delay,fn)
    if delay == 0 then fn(); return end
    local timer={at=now+delay, fn=fn, killed=false}
    function timer:kill() self.killed=true end
    timeouts[#timeouts+1]=timer
    return timer
end
fake.add_periodic_timer=function(_,fn) timers[#timers+1]=fn end
fake.command_native=function(request)
    for _,v in ipairs(request.args) do assert(v ~= '--harden') end
    return {status=0,stdout=utils.format_json({endpoint='existing-pipe'})}
end
fake.command_native_async=function(args,fn)
    assert(args.playback_only == false and args.detach == false)
    assert(args.args[2] == '-I' and args.args[3] == '-m')
    starts=starts+1; callbacks[starts]=fn; launches[starts]=args.args; return starts
end
fake.abort_async_command=function(id) callbacks[id](false, {status=-1}, 'killed') end
package.loaded.mp=fake
local config = {}
package.loaded['mp.options']={read_options=function(o)
    o.backend_dir=os.getenv('REFRAME_TEST_ROOT'); o.python_path='fake-python'
    for k,v in pairs(config) do o[k]=v end
end}
local original_info=utils.file_info
utils.file_info=function() return {is_file=true} end
local ok,err=pcall(function()
    dofile(os.getenv('REFRAME_TEST_ROOT')..'/mpv/reframe.lua')
    assert(overlays[1].data:find('Reframe: OFF',1,true))
    local initial_updates=overlays[1].updates
    observers['osd-dimensions']()
    assert(overlays[1].updates == initial_updates+1)
    assert(timeouts[1].at == 5)
    now=5; timeouts[1].fn()
    assert(overlays[1].removed)
    observers['fullscreen']()
    assert(overlays[1].removed)
    local function event(s, seq, kind, fields)
        local d=fields or {};d.protocol=1;d.session=s;d.seq=seq;d.type=kind
        if kind == 'hello' then d.backend_version='0.1.0';d.pid=123 end
        handlers['reframe-backend'](utils.format_json(d))
    end
    for i=1,20 do
        handlers['reframe-enable']();handlers['reframe-enable']()
        assert(starts==i)
        assert(overlays[1].data:find('Reframe: STARTING',1,true))
        assert(not overlays[1].removed)
        local id=values['user-data/reframe'].session
        event(id,1,'hello')
        event(id,2,'acquire',{view_epoch=1,saved={panscan=.2,['video-align-x']=.3,cache=(i % 2 == 0 and true or 'auto'),['video-timing-offset']=0,['keepaspect-window']=true}})
        assert(crops[#crops].type=='acquire-ack')
        event(id,3,'state',{state='loading'})
        assert(overlays[1].data:find('STARTING (loading)',1,true))
        event(id,4,'state',{state='waiting'})
        assert(overlays[1].data:find('SUSPENDED (waiting)',1,true))
        event(id,5,'state',{state='active'})
        assert(overlays[1].data:find('Reframe: ON',1,true))
        local active_timer=timeouts[#timeouts]
        event(id,6,'state',{state='active'})
        assert(timeouts[#timeouts] == active_timer)
        event(id,7,'state',{state='suspended'})
        assert(active_timer.killed)
        assert(overlays[1].data:find('Reframe: SUSPENDED',1,true))
        handlers['reframe-stop']()
        assert(overlays[1].data:find('OFF (stopping)',1,true))
        callbacks[i](true,{status=0})
        assert(values.panscan==.2 and values['video-align-x']==.3)
        assert(values['user-data/reframe'].state=='disabled')
    end
    do
        -- Framing mode: stored while stopped, passed at launch, forwarded
        -- after hello, and never sent for an unknown mode.
        assert(bindings.mode and bindings.mode.key=='Ctrl+g')
        assert(values['user-data/reframe'].mode=='follow')
        bindings.mode.fn()
        assert(values['user-data/reframe'].mode=='group')
        handlers['reframe-enable']()
        local id=values['user-data/reframe'].session
        local launch=launches[starts]
        local function arg(name)
            for k,v in ipairs(launch) do if v==name then return launch[k+1] end end
        end
        assert(arg('--mode')=='group')
        assert(arg('--group-conf-floor')=='0.5' and arg('--group-coverage-floor')=='0.7')
        assert(arg('--group-relaxed-floor')=='0.5' and arg('--group-pair-floor')=='0.2')
        assert(arg('--group-join')=='0.3' and arg('--group-leave')=='0.7' and arg('--group-switch')=='0.6')
        local before=#crops
        handlers['reframe-mode']('follow')
        assert(#crops==before)
        event(id,1,'hello')
        assert(crops[#crops].type=='mode' and crops[#crops].mode=='follow')
        event(id,2,'state',{state='active'})
        assert(overlays[1].data:find('Reframe: ON',1,true) and not overlays[1].data:find('group',1,true))
        handlers['reframe-mode']('toggle')
        assert(crops[#crops].type=='mode' and crops[#crops].mode=='group')
        assert(overlays[1].data:find('ON (group)',1,true))
        local sent=#crops
        handlers['reframe-mode']('group')
        handlers['reframe-mode']('sideways')
        assert(#crops==sent and values['user-data/reframe'].mode=='group')
        handlers['reframe-stop']()
        callbacks[starts](true,{status=0})
        handlers['reframe-mode']('follow')
        assert(values['user-data/reframe'].mode=='follow')
    end
    do
        -- Debug overlay: stored while stopped, passed at launch, forwarded
        -- after hello, and never sent for an unknown value or a repeat.
        assert(bindings.debug and bindings.debug.key=='Ctrl+d')
        assert(values['user-data/reframe'].debug==false)
        bindings.debug.fn()
        assert(values['user-data/reframe'].debug==true)
        handlers['reframe-enable']()
        local id=values['user-data/reframe'].session
        local launch=launches[starts]
        local function has(name)
            for _,v in ipairs(launch) do if v==name then return true end end
            return false
        end
        assert(has('--debug-osd'))
        local before=#crops
        handlers['reframe-debug']('off')
        assert(#crops==before and values['user-data/reframe'].debug==false)
        event(id,1,'hello')
        assert(crops[#crops].type=='debug' and crops[#crops].enabled==false)
        event(id,2,'state',{state='active'})
        handlers['reframe-debug']('toggle')
        assert(crops[#crops].type=='debug' and crops[#crops].enabled==true)
        local sent=#crops
        handlers['reframe-debug']('on')
        handlers['reframe-debug']('sideways')
        assert(#crops==sent and values['user-data/reframe'].debug==true)
        handlers['reframe-debug']('off')
        assert(crops[#crops].type=='debug' and crops[#crops].enabled==false)
        handlers['reframe-stop']()
        callbacks[starts](true,{status=0})
        handlers['reframe-debug']('off')
        assert(values['user-data/reframe'].debug==false)
    end
    handlers['reframe-enable']()
    local id=values['user-data/reframe'].session
    event('old-session',1,'error',{message='stale'})
    assert(values['user-data/reframe'].state=='starting')
    event(id,9007199254740992,'error',{message='overflow'})
    event(id,999,'unknown')
    event(id,999,'state',{state={}})
    event(id,999,'released',{view_epoch=.5})
    assert(values['user-data/reframe'].state=='starting')
    event(id,1,'hello')
    event(id,2,'acquire',{view_epoch=1,saved={panscan=.4,['video-align-x']=.1,cache='auto',['video-timing-offset']=0,['keepaspect-window']=true}})
    -- A crash must restore even without released/stop.
    callbacks[starts](false,{status=1},'crash')
    assert(values.panscan==.4)
    assert(values['user-data/reframe'].state=='error')
    assert(overlays[1].data:find('Reframe: ERROR',1,true))
    -- Stop a hung startup, then verify cancellation after the grace period.
    handlers['reframe-enable']();handlers['reframe-stop']()
    now=11;timers[1]()
    assert(values['user-data/reframe'].state=='disabled')
    -- Cold startup gets the full handshake budget, then a specific error.
    handlers['reframe-enable']()
    local born=now
    now=born+11;timers[1]()
    assert(values['user-data/reframe'].state=='starting')
    now=born+31;timers[1]()
    assert(values['user-data/reframe'].reason:find('Backend handshake timed out',1,true))
    callbacks[starts](true,{status=0})
    -- Healthy loading can exceed the former 120s limit, but remains bounded.
    handlers['reframe-enable']()
    id=values['user-data/reframe'].session
    event(id,1,'hello')
    event(id,2,'state',{state='loading'})
    local loading=now
    for second=1,301 do
        now=loading+second
        -- Repeated loading reports must not restart the loading deadline.
        event(id,2*second+1,'state',{state='loading'})
        event(id,2*second+2,'heartbeat',{phase='loading',controller_progress=0,stalled=false})
        timers[1]()
        if second<=300 then assert(values['user-data/reframe'].state=='loading') end
    end
    assert(values['user-data/reframe'].reason:find('Model loading timed out after 300s',1,true))
    callbacks[starts](true,{status=0})
    -- A longer loading budget never masks a lost heartbeat.
    handlers['reframe-enable']()
    id=values['user-data/reframe'].session
    event(id,1,'hello');event(id,2,'state',{state='loading'})
    now=now+6;timers[1]()
    assert(values['user-data/reframe'].reason:find('Backend heartbeat timed out',1,true))
    callbacks[starts](true,{status=0})
    -- User settings apply to the next enable, including validation.
    config.startup_timeout=60;config.loading_timeout=600
    handlers['reframe-enable']()
    born=now;now=born+31;timers[1]()
    assert(values['user-data/reframe'].state=='starting')
    id=values['user-data/reframe'].session
    event(id,1,'hello');event(id,2,'state',{state='loading'})
    for second=1,301 do
        now=born+31+second
        event(id,second+2,'heartbeat',{phase='loading',controller_progress=0,stalled=false})
        timers[1]()
    end
    assert(values['user-data/reframe'].state=='loading')
    event(id,304,'state',{state='active'})
    now=now+1;timers[1]()
    assert(values['user-data/reframe'].state=='active')
    handlers['reframe-stop']();callbacks[starts](true,{status=0})
    for _,bad in ipairs({0,-1,math.huge,0/0}) do
        config.loading_timeout=bad
        local before=starts
        handlers['reframe-enable']()
        assert(starts==before and values['user-data/reframe'].state=='error')
        assert(values['user-data/reframe'].reason:find('loading_timeout must be',1,true))
    end
    -- The shared detector is the default and its settings reach the backend.
    local function flag(args, name)
        for i,v in ipairs(args) do if v==name then return args[i+1] end end
    end
    config.loading_timeout=300 -- the loop above left an invalid value in the shared table
    local before=starts
    handlers['reframe-enable']()
    assert(starts==before+1)
    assert(flag(launches[starts],'--detector')=='shared' and flag(launches[starts],'--detector-idle')=='600')
    handlers['reframe-stop']();callbacks[starts](true,{status=0})
    config.shared_detector=false; config.detector_idle_timeout=0
    before=starts
    handlers['reframe-enable']()
    assert(starts==before+1)
    assert(flag(launches[starts],'--detector')=='local' and flag(launches[starts],'--detector-idle')=='0')
    handlers['reframe-stop']();callbacks[starts](true,{status=0})
    for _,bad in ipairs({-1,86401,1.5,math.huge,0/0}) do
        config.detector_idle_timeout=bad
        before=starts
        handlers['reframe-enable']()
        assert(starts==before and values['user-data/reframe'].state=='error')
        assert(values['user-data/reframe'].reason:find('detector_idle_timeout must be',1,true))
    end
    config.shared_detector=true; config.detector_idle_timeout=600
    -- The configuration opt-out removes this script's own overlay only.
    package.loaded['mp.options']={read_options=function(o)
        o.status_indicator=false
    end}
    dofile(os.getenv('REFRAME_TEST_ROOT')..'/mpv/reframe.lua')
    assert(overlays[2].removed and overlays[2].updates == 0)
end)
utils.file_info=original_info
if ok then print('LUA_LIFECYCLE_PASS') else print(tostring(err)) end
real.commandv('quit',ok and '0' or '1')
