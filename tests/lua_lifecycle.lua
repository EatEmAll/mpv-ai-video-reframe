-- Execute with mpv's own interpreter; replace only the script API under test.
local real = require 'mp'
local utils = require 'mp.utils'
local handlers, callbacks, timers = {}, {}, {}
local values, crops, now, starts = {}, {}, 0, 0
local fake = {msg={error=function() end}}
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
fake.add_timeout=function(_,fn) fn() end
fake.add_periodic_timer=function(_,fn) timers[#timers+1]=fn end
fake.command_native_async=function(args,fn)
    assert(args.playback_only == false and args.detach == false)
    assert(args.args[2] == '-m')
    starts=starts+1; callbacks[starts]=fn; return starts
end
fake.abort_async_command=function(id) callbacks[id](false, {status=-1}, 'killed') end
package.loaded.mp=fake
package.loaded['mp.options']={read_options=function(o) o.backend_dir=os.getenv('REFRAME_TEST_ROOT'); o.python_path='fake-python' end}
local original_info=utils.file_info
utils.file_info=function() return {is_file=true} end
local ok,err=pcall(function()
    dofile(os.getenv('REFRAME_TEST_ROOT')..'/mpv/reframe.lua')
    local function event(s, seq, kind, fields)
        local d=fields or {};d.protocol=1;d.session=s;d.seq=seq;d.type=kind
        handlers['reframe-backend'](utils.format_json(d))
    end
    for i=1,20 do
        handlers['reframe-enable']();handlers['reframe-enable']()
        assert(starts==i)
        local id=values['user-data/reframe'].session
        event(id,1,'hello')
        event(id,2,'acquire',{view_epoch=1,saved={panscan=.2,['video-align-x']=.3,cache=(i % 2 == 0 and true or 'auto'),['video-timing-offset']=0,['keepaspect-window']=true}})
        assert(crops[#crops].type=='acquire-ack')
        handlers['reframe-stop']()
        callbacks[i](true,{status=0})
        assert(values.panscan==.2 and values['video-align-x']==.3)
        assert(values['user-data/reframe'].state=='disabled')
    end
    handlers['reframe-enable']()
    local id=values['user-data/reframe'].session
    event('old-session',1,'error',{message='stale'})
    assert(values['user-data/reframe'].state=='starting')
    event(id,1,'hello')
    event(id,2,'acquire',{view_epoch=1,saved={panscan=.4,['video-align-x']=.1,cache='auto',['video-timing-offset']=0,['keepaspect-window']=true}})
    -- A crash must restore even without released/stop.
    callbacks[starts](false,{status=1},'crash')
    assert(values.panscan==.4)
    assert(values['user-data/reframe'].state=='error')
    -- Stop a hung startup, then verify cancellation after the grace period.
    handlers['reframe-enable']();handlers['reframe-stop']()
    now=6;timers[1]()
    assert(values['user-data/reframe'].state=='disabled')
end)
utils.file_info=original_info
if ok then print('LUA_LIFECYCLE_PASS') else print(tostring(err)) end
real.commandv('quit',ok and '0' or '1')
