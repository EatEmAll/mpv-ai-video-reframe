-- Execute with mpv's own interpreter; replace only the script API under test.
local real = require 'mp'
local utils = require 'mp.utils'
local handlers, callbacks, timers = {}, {}, {}
local values, crops, now, starts = {}, {}, 0, 0
local overlays, observers, timeouts = {}, {}, {}
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
fake.add_key_binding=function() end
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
    starts=starts+1; callbacks[starts]=fn; return starts
end
fake.abort_async_command=function(id) callbacks[id](false, {status=-1}, 'killed') end
package.loaded.mp=fake
package.loaded['mp.options']={read_options=function(o) o.backend_dir=os.getenv('REFRAME_TEST_ROOT'); o.python_path='fake-python' end}
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
