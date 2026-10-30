-- Isolated idle player only: exercise real Lua -> preflight -> backend -> stop.
local mp = require 'mp'
local started, stopping = false, false
local deadline = mp.get_time() + 12
mp.add_timeout(.1, function() mp.commandv('script-message-to', 'reframe', 'reframe-enable') end)
mp.add_periodic_timer(.1, function()
    local state = mp.get_property_native('user-data/reframe') or {}
    if state.state == 'waiting' and not stopping then
        started, stopping = true, true
        mp.commandv('script-message-to', 'reframe', 'reframe-stop')
    elseif started and state.state == 'disabled' then
        print('LUA_SECURITY_INTEGRATION_PASS')
        mp.commandv('quit', '0')
    elseif state.state == 'error' or mp.get_time() > deadline then
        print('LUA_SECURITY_INTEGRATION_FAIL '..tostring(state.state)..' '..tostring(state.reason))
        mp.commandv('quit', '1')
    end
end)
