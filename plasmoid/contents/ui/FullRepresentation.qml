import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents3
import org.kde.kirigami as Kirigami

// Desktop widget and panel popup: one minimal rounded card with the device list.
Item {
    id: full

    property var devices: []
    property var providers: []
    property string errorText: ""
    property bool loaded: false
    property bool busy: false
    property var mutedDevices: []
    property string pinnedDevice: ""

    signal refreshRequested
    signal toggleMute(string id)
    signal togglePin(string id)
    signal hideDevice(string id)

    readonly property bool onDesktop: Plasmoid.formFactor === PlasmaCore.Types.Planar
    // On the desktop we draw our own card (no Plasma frame). In a panel popup the popup
    // already has a frame, so a card is only added when a fixed light/dark look is chosen.
    readonly property bool drawCard: onDesktop || pl.forced
    readonly property real pad: Kirigami.Units.gridUnit * (drawCard ? 1.1 : 0.4)

    readonly property string providerProblems: {
        var lines = [];
        for (var i = 0; i < providers.length; ++i) {
            var p = providers[i];
            if (p.important && !p.ok && p.error) {
                lines.push(p.name + ": " + p.error);
            }
        }
        return lines.join("\n");
    }

    BatteryColors {
        id: pl
        mode: Plasmoid.configuration.appearance
    }

    Layout.minimumWidth: Kirigami.Units.gridUnit * 15
    Layout.minimumHeight: Kirigami.Units.gridUnit * 8
    Layout.preferredWidth: Kirigami.Units.gridUnit * 19
    Layout.preferredHeight: Math.min(Kirigami.Units.gridUnit * 32, Math.max(Kirigami.Units.gridUnit * 8, body.implicitHeight + pad * 2))

    Rectangle {
        anchors.fill: parent
        visible: full.drawCard
        radius: Kirigami.Units.gridUnit * 1.4
        color: pl.card
        border.width: 1
        border.color: pl.line
    }

    ColumnLayout {
        id: body
        anchors {
            fill: parent
            margins: full.pad
        }
        spacing: Kirigami.Units.smallSpacing

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            implicitHeight: contentHeight
            visible: full.devices.length > 0
            model: full.devices
            clip: true
            interactive: contentHeight > height
            boundsBehavior: Flickable.StopAtBounds

            delegate: DeviceDelegate {
                width: ListView.view.width
                device: modelData
                pal: pl
                muted: full.mutedDevices.indexOf(modelData.id) >= 0
                pinned: full.pinnedDevice === modelData.id
                showDivider: index < full.devices.length - 1
                onToggleMute: full.toggleMute(modelData.id)
                onTogglePin: full.togglePin(modelData.id)
                onHideDevice: full.hideDevice(modelData.id)
            }
        }

        ColumnLayout {
            id: empty
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: full.loaded && full.devices.length === 0
            spacing: Kirigami.Units.smallSpacing

            Item {
                Layout.fillHeight: true
            }
            Kirigami.Icon {
                source: "preferences-desktop-peripherals"
                isMask: true
                color: pl.inkSoft
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Kirigami.Units.iconSizes.large
                Layout.preferredHeight: Kirigami.Units.iconSizes.large
            }
            PlasmaComponents3.Label {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                text: i18n("No wireless devices found")
                color: pl.ink
                font.pixelSize: Math.round(Kirigami.Units.gridUnit * 0.95)
            }
            PlasmaComponents3.Label {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                text: full.errorText !== "" ? full.errorText
                    : (full.providerProblems !== "" ? full.providerProblems
                    : i18n("Connect a Bluetooth or wireless device that reports its battery."))
                color: full.errorText !== "" ? pl.low : pl.inkSoft
                font.pixelSize: Math.round(Kirigami.Units.gridUnit * 0.75)
            }
            Item {
                Layout.fillHeight: true
            }
        }

        PlasmaComponents3.Label {
            Layout.fillWidth: true
            visible: full.errorText !== "" && full.devices.length > 0
            text: full.errorText
            wrapMode: Text.WordWrap
            color: pl.low
            font.pixelSize: Math.round(Kirigami.Units.gridUnit * 0.75)
        }
    }

    PlasmaComponents3.BusyIndicator {
        anchors.centerIn: parent
        running: visible
        visible: !full.loaded
    }
}
