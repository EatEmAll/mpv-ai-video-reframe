-- Person-following controls for stock mpv. Lua 5.1; no external Lua packages.
local mp = require 'mp'
local utils = require 'mp.utils'
local options = require 'mp.options'
local o = {backend_dir='', python_path='', autostart=false, toggle_key='Ctrl+r',
           rate=10, model='', debug_osd=false}
options.read_options(o, 'reframe')
local windows = package.config:sub(1,1) == '\\'
local current, counter, endpoint = nil, 0, nil
local enabled, pending_enable = false, false
local start
local saved_names = {panscan='number', ['video-align-x']='number', cache='string',
                     ['video-timing-offset']='number', ['keepaspect-window']='boolean'}
local function show(text) mp.osd_message('Reframe: '..text, 3) end
local function publish(s, state, reason)
    if s then s.state, s.reason = state, reason or '' end
    mp.set_property_native('user-data/reframe', {state=state, reason=reason or '',
        session=s and s.id or '', log_dir=s and s.output or ''})
end
local function control(s, kind, fields)
    s.sent = s.sent + 1
    local data = fields or {}
    data.protocol, data.session, data.seq, data.type = 1, s.id, s.sent, kind
    mp.commandv('script-message', 'reframe-control', utils.format_json(data))
end
local function restore(s)
    if not s.saved then return end
    for name,value in pairs(s.saved) do
        local ok, err = mp.set_property_native(name, value)
        if not ok then mp.msg.error('Restore '..name..': '..tostring(err)) end
    end
end
local function stop(reason)
    enabled, pending_enable = false, false
    local s = current
    if not s or s.stopping then return end
    s.stopping, s.stop_at = true, mp.get_time()
    publish(s, 'stopping', reason)
    control(s, 'stop', {reason=reason or 'user'})
end
local function fail(s, text)
    s.failure = text
    stop(text)
    show(text)
end
local function valid_saved(t)
    if type(t) ~= 'table' then return false end
    for name,kind in pairs(saved_names) do
        local v = t[name]
        if type(v) ~= kind and not (name == 'cache' and type(v) == 'boolean') then return false end
        if kind == 'number' and (v ~= v or math.abs(v) == math.huge) then return false end
    end
    for name in pairs(t) do if not saved_names[name] then return false end end
    return true
end
mp.register_script_message('reframe-backend', function(raw)
    local s = current
    if not s or type(raw) ~= 'string' or #raw > 16384 then return end
    local d = utils.parse_json(raw)
    if type(d) ~= 'table' or d.protocol ~= 1 or d.session ~= s.id or
       type(d.seq) ~= 'number' or d.seq % 1 ~= 0 or d.seq > 9007199254740991 or d.seq <= s.received then return end
    local schemas = {hello={backend_version=true,pid=true},
        heartbeat={phase=true,controller_progress=true,stalled=true},
        state={state=true,reason=true,model=true,log_dir=true},
        acquire={view_epoch=true,saved=true}, released={view_epoch=true},
        error={code=true,message=true}}
    local schema = schemas[d.type]
    if not schema then return end
    local common = {protocol=true,session=true,seq=true,type=true}
    for key in pairs(d) do if not common[key] and not schema[key] then return end end
    local function text(value) return type(value) == 'string' and #value <= 1024 end
    if d.type == 'hello' and (not text(d.backend_version) or type(d.pid) ~= 'number' or
       d.pid < 1 or d.pid % 1 ~= 0 or d.pid > 9007199254740991) then return end
    if d.type == 'state' then
        local states = {starting=true, waiting=true, loading=true, ready=true, active=true, suspended=true}
        if not states[d.state] or (d.reason ~= nil and (type(d.reason) ~= 'string' or #d.reason > 1024)) then return end
        if (d.model ~= nil and not text(d.model)) or (d.log_dir ~= nil and not text(d.log_dir)) then return end
    end
    if d.type == 'error' and (not text(d.message) or (d.code ~= nil and not text(d.code))) then return end
    if d.type == 'heartbeat' and (type(d.stalled) ~= 'boolean' or not text(d.phase) or
       type(d.controller_progress) ~= 'number' or d.controller_progress ~= d.controller_progress or
       math.abs(d.controller_progress) == math.huge) then return end
    if (d.type == 'acquire' or d.type == 'released') and
       (type(d.view_epoch) ~= 'number' or d.view_epoch % 1 ~= 0 or
        d.view_epoch < 1 or d.view_epoch > 9007199254740991) then return end
    if d.type == 'acquire' and (d.view_epoch <= s.epoch or not valid_saved(d.saved)) then return end
    if d.type == 'released' and d.view_epoch ~= s.epoch then return end
    s.received, s.last = d.seq, mp.get_time()
    if d.type == 'hello' then
        s.hello = true
    elseif d.type == 'heartbeat' then
        if d.stalled and not s.stopping then fail(s, 'Controller stopped responding') end
    elseif d.type == 'state' and not s.stopping then
        if d.state == 'loading' and not s.loading then s.loading = mp.get_time() end
        if d.state ~= 'loading' then s.loading = nil end
        publish(s, d.state, d.reason)
    elseif d.type == 'acquire' then
        if s.stopping then return end
        if type(d.view_epoch) ~= 'number' or d.view_epoch <= s.epoch or not valid_saved(d.saved) then
            fail(s, 'Invalid view acquisition')
            return
        end
        s.epoch, s.saved = d.view_epoch, d.saved
        control(s, 'acquire-ack', {view_epoch=d.view_epoch})
    elseif d.type == 'released' and d.view_epoch == s.epoch then
        s.saved = nil
    elseif d.type == 'error' then
        fail(s, tostring(d.message or 'Backend failed'))
    end
end)
local function executable()
    if o.python_path ~= '' then return o.python_path end
    return utils.join_path(o.backend_dir, windows and '.venv/Scripts/python.exe' or '.venv/bin/python')
end
start = function()
    if current then
        if current.stopping then pending_enable = true end
        return
    end
    options.read_options(o, 'reframe')
    if o.backend_dir == '' or not utils.file_info(utils.join_path(o.backend_dir, 'pyproject.toml')) then
        show('Set backend_dir in script-opts/reframe.conf to the prepared backend directory')
        return
    end
    if o.rate < 5 or o.rate > 15 or o.rate % 1 ~= 0 then show('rate must be an integer from 5 to 15'); return end
    if not utils.file_info(executable()) then show('Python environment missing; prepare the backend first'); return end
    local function security(command, pipe, harden)
        local args = {executable(), '-I', '-m', 'reframe.security', command,
            '--player-pid', tostring(utils.getpid()), '--backend', o.backend_dir,
            '--python', executable()}
        if pipe then args[#args+1]='--endpoint'; args[#args+1]=pipe end
        if harden then args[#args+1]='--harden' end
        local result = mp.command_native({name='subprocess', args=args, playback_only=false,
            capture_stdout=true, capture_stderr=true, capture_size=4096})
        if not result or result.status ~= 0 then return nil end
        local data = utils.parse_json(result.stdout)
        return type(data) == 'table' and data.endpoint or nil
    end
    local pipe = mp.get_property('input-ipc-server', '')
    if pipe == '' then
        endpoint = security('prepare')
        if not endpoint then show('IPC preparation failed; check trusted paths and permissions'); return end
        local ok = mp.set_property('input-ipc-server', endpoint)
        if not ok then show('Cannot enable private IPC'); return end
        pipe = endpoint
    end
    if not security('verify', pipe, pipe == endpoint) then
        if pipe == endpoint then mp.set_property('input-ipc-server', '') end
        show('IPC ownership or permissions rejected; see SECURITY.md')
        return
    end
    counter = counter + 1
    local id = tostring(utils.getpid())..'-'..tostring(os.time())..'-'..counter
    local s = {id=id, sent=0, received=0, epoch=0, last=mp.get_time(), born=mp.get_time()}
    s.output = utils.join_path(o.backend_dir, 'runs/lua/'..id)
    current, enabled = s, true
    publish(s, 'starting')
    local args = {executable(), '-I', '-m', 'reframe', 'attach', '--managed', '--pipe', pipe,
        '--player-pid', tostring(utils.getpid()), '--session-id', id, '--lua-client', mp.get_script_name(), '--backend-dir', o.backend_dir,
        '--output-dir', s.output, '--rate', tostring(o.rate)}
    if o.model ~= '' then args[#args+1]='--model'; args[#args+1]=o.model end
    if o.debug_osd then args[#args+1]='--debug-osd' end
    s.handle = mp.command_native_async({name='subprocess', args=args, playback_only=false,
        detach=false, capture_stdout=true, capture_stderr=true, capture_size=65536}, function(ok, result, err)
        if current ~= s then return end
        restore(s)
        mp.add_timeout(0, function()
            if current ~= s then return end
            restore(s)
            local failure = s.failure
            if not s.stopping then
                local detail = result and result.stderr and result.stderr:match('([^\r\n]+)[\r\n]*$')
                failure = failure or ('Backend exited: '..tostring(detail and detail:sub(-500) or err or (result and result.status)))
                if result and result.stderr then mp.msg.error(result.stderr) end
                show(failure)
            end
            current, enabled = nil, false
            publish(s, failure and 'error' or 'disabled', failure)
            if pending_enable then pending_enable=false; start() end
        end)
    end)
end
local function toggle()
    if current and not current.stopping then stop('user') else start() end
end
mp.add_periodic_timer(1, function()
    local s = current
    if not s then return end
    local now = mp.get_time()
    if s.stopping then
        if now - s.stop_at > 5 and not s.aborted then
            s.aborted=true
            if s.handle then mp.abort_async_command(s.handle) end
        elseif now - s.stop_at > 10 and not s.reported then
            s.reported=true; show('Waiting for backend exit; another backend will not be started')
        end
    elseif (not s.hello and now-s.born > 10) or (s.hello and now-s.last > 5) or
           (s.loading and now-s.loading > 120) then
        fail(s, 'Backend startup or heartbeat timed out; see '..s.output)
    end
end)
mp.add_key_binding(o.toggle_key ~= '' and o.toggle_key or nil, 'toggle', toggle)
mp.register_script_message('reframe-toggle', toggle)
mp.register_script_message('reframe-enable', start)
mp.register_script_message('reframe-stop', function() stop('user') end)
mp.register_script_message('reframe-status', function()
    local s=current
    show(s and (s.state..' '..(s.reason or '')..' '..s.output) or 'disabled')
end)
mp.register_event('shutdown', function()
    if current then control(current, 'stop', {reason='shutdown'}) end
    -- mpv owns endpoint lifetime. Never unlink an endpoint by pathname here.
end)
if o.autostart then mp.add_timeout(0, start) end
