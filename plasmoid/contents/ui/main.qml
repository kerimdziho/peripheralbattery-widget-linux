import QtQuick
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.kirigami as Kirigami

import "lib.js" as Lib

PlasmoidItem {
    id: root

    // ---- state ---------------------------------------------------------
    property var devices: []
    property var providers: []
    property string errorText: ""
    property bool loaded: false
    property bool busy: false

    readonly property string scriptPath: Lib.urlToPath(Qt.resolvedUrl("../code/pbattery.py"))
    readonly property var lowest: devices.length > 0 ? devices[0] : null
    // The device the panel shows: the one pinned from the list, otherwise the weakest.
    readonly property var shownDevice: {
        var pin = Plasmoid.configuration.panelDevice;
        if (pin) {
            for (var i = 0; i < devices.length; ++i) {
                if (devices[i].id === pin) {
                    return devices[i];
                }
            }
        }
        return lowest;
    }

    // ---- plasmoid properties -------------------------------------------
    Plasmoid.icon: shownDevice ? Lib.batteryIcon(shownDevice.percent, shownDevice.charging) : "preferences-desktop-peripherals"
    // On the desktop the widget draws its own minimal card instead of Plasma's frame.
    Plasmoid.backgroundHints: Plasmoid.formFactor === PlasmaCore.Types.Planar
        ? PlasmaCore.Types.NoBackground
        : PlasmaCore.Types.DefaultBackground
    toolTipMainText: i18n("Peripheral Battery")
    toolTipSubText: {
        if (!loaded) {
            return i18n("Looking for devices…");
        }
        if (devices.length === 0) {
            return i18n("No wireless devices with battery information found");
        }
        var lines = [];
        for (var i = 0; i < devices.length; ++i) {
            var d = devices[i];
            lines.push(d.name + ": " + (d.approx ? "~" : "") + d.percent + "%" + (d.charging ? " ⚡" : ""));
        }
        return lines.join("\n");
    }

    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: i18n("Refresh Now")
            icon.name: "view-refresh"
            onTriggered: root.refresh()
        }
    ]

    // ---- backend -------------------------------------------------------
    function refresh() {
        if (busy) {
            return;
        }
        busy = true;
        busyGuard.restart();
        var settings = Lib.buildSettings(Plasmoid.configuration);
        var extra = Plasmoid.configuration.sendNotifications ? "--notify" : "";
        backend.connectSource(Lib.backendCommand(scriptPath, "poll", settings, extra));
    }

    function handleResult(exitCode, stdout, stderr) {
        busy = false;
        busyGuard.stop();
        loaded = true;
        if (exitCode !== 0 && !stdout) {
            if (exitCode === 127) {
                errorText = i18n("Python 3 is required but was not found.");
            } else {
                errorText = stderr ? String(stderr).trim() : i18n("The battery helper failed (exit code %1).", exitCode);
            }
            return;
        }
        try {
            var result = JSON.parse(stdout);
            if (result.error) {
                errorText = result.error;
                return;
            }
            errorText = "";
            devices = result.devices || [];
            providers = result.providers || [];
        } catch (e) {
            errorText = i18n("Could not read the helper's answer: %1", String(e));
        }
    }

    function setListEntry(key, id, on) {
        var list = Lib.toArray(Plasmoid.configuration[key]).filter(function (x) {
            return x !== id;
        });
        if (on) {
            list.push(id);
        }
        Plasmoid.configuration[key] = list;
    }

    function toggleMute(id) {
        var muted = Lib.toArray(Plasmoid.configuration.mutedDevices).indexOf(id) >= 0;
        setListEntry("mutedDevices", id, !muted);
    }

    function togglePin(id) {
        Plasmoid.configuration.panelDevice = Plasmoid.configuration.panelDevice === id ? "" : id;
    }

    function hideDevice(id) {
        setListEntry("hiddenDevices", id, true);
    }

    Plasma5Support.DataSource {
        id: backend
        engine: "executable"
        connectedSources: []
        interval: 0
        onNewData: function (sourceName, data) {
            disconnectSource(sourceName);
            root.handleResult(data["exit code"], data["stdout"], data["stderr"]);
        }
    }

    Timer {
        id: pollTimer
        interval: Math.max(15, Plasmoid.configuration.pollSeconds) * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    // If the helper never answers (hung D-Bus call...), allow the next poll.
    Timer {
        id: busyGuard
        interval: 30000
        onTriggered: root.busy = false
    }

    // Apply settings changes quickly instead of waiting for the next tick.
    Timer {
        id: settingsDebounce
        interval: 800
        onTriggered: root.refresh()
    }

    Connections {
        target: Plasmoid.configuration
        ignoreUnknownSignals: true
        function onValueChanged(key, value) {
            settingsDebounce.restart();
        }
    }

    onExpandedChanged: {
        if (expanded) {
            refresh();
        }
    }

    // ---- views ---------------------------------------------------------
    compactRepresentation: CompactRepresentation {
        device: root.shownDevice
        expanded: root.expanded
        display: Plasmoid.configuration.panelDisplay
        onActivated: root.expanded = !root.expanded
    }

    fullRepresentation: FullRepresentation {
        devices: root.devices
        providers: root.providers
        errorText: root.errorText
        loaded: root.loaded
        busy: root.busy
        mutedDevices: Lib.toArray(Plasmoid.configuration.mutedDevices)
        pinnedDevice: Plasmoid.configuration.panelDevice
        onRefreshRequested: root.refresh()
        onToggleMute: function (id) {
            root.toggleMute(id);
        }
        onTogglePin: function (id) {
            root.togglePin(id);
        }
        onHideDevice: function (id) {
            root.hideDevice(id);
        }
    }
}
