.pragma library

// Helpers shared by the widget and its settings pages. No i18n in here
// (pragma libraries have no translation context) - keep strings in QML.

function shellQuote(s) {
    return "'" + String(s).replace(/'/g, "'\\''") + "'";
}

function urlToPath(url) {
    var s = String(url);
    if (s.indexOf("file://") === 0) {
        s = s.substring(7);
    }
    return decodeURIComponent(s);
}

function toArray(list) {
    var out = [];
    if (list === undefined || list === null) {
        return out;
    }
    for (var i = 0; i < list.length; ++i) {
        out.push(String(list[i]));
    }
    return out;
}

function parseList(s) {
    return String(s).split(/[\s,;]+/).map(function (x) {
        return parseInt(x, 10);
    }).filter(function (x) {
        return !isNaN(x);
    });
}

// Translate widget configuration (kcfg keys) into the backend's settings JSON.
function buildSettings(c) {
    return {
        poll_seconds: c.pollSeconds,
        thresholds: parseList(c.thresholds),
        critical_below: c.criticalBelow,
        charged_threshold: c.chargedEnabled ? c.chargedThreshold : 0,
        hysteresis: c.hysteresis,
        include_system_battery: c.includeSystemBattery,
        demo: c.demoMode,
        providers: {
            upower: true,
            bluez: c.useBluez,
            headsetcontrol: c.useHeadsetcontrol
        },
        muted: toArray(c.mutedDevices),
        hidden: toArray(c.hiddenDevices),
        notify: {
            enabled: c.sendNotifications,
            desktop: true,
            persistent: c.persistent,
            critical_sticky: c.criticalSticky,
            sound: c.playSound,
            ntfy: {
                enabled: c.ntfyEnabled,
                server: c.ntfyServer,
                topic: c.ntfyTopic,
                token: c.ntfyToken
            }
        }
    };
}

function backendCommand(scriptPath, subcommand, settings, extraArgs) {
    return "python3 " + shellQuote(scriptPath) + " " + subcommand + " --no-config"
        + (extraArgs ? " " + extraArgs : "")
        + " --settings-json " + shellQuote(JSON.stringify(settings));
}

function iconForKind(kind) {
    switch (kind) {
    case "mouse": return "input-mouse";
    case "keyboard": return "input-keyboard";
    case "headphones": return "audio-headphones";
    case "headset": return "audio-headset";
    case "gamepad": return "input-gaming";
    case "phone": return "phone";
    case "tablet":
    case "pen": return "input-tablet";
    case "touchpad": return "input-touchpad";
    case "speaker": return "audio-speakers";
    case "laptop": return "computer-laptop";
    default: return "preferences-desktop-peripherals";
    }
}

// Breeze ships battery-000 ... battery-100 (+ "-charging" variants).
function batteryIcon(percent, charging) {
    if (percent === undefined || percent === null) {
        return "battery-missing";
    }
    var bucket = Math.max(0, Math.min(100, Math.round(percent / 10) * 10));
    var name = "battery-" + (bucket < 100 ? "0" : "") + (bucket < 10 ? "0" : "") + bucket;
    return charging ? name + "-charging" : name;
}

// "low" | "medium" | "good" | "charging"
function levelClass(percent, charging) {
    if (charging) {
        return "charging";
    }
    if (percent <= 15) {
        return "low";
    }
    if (percent <= 40) {
        return "medium";
    }
    return "good";
}
