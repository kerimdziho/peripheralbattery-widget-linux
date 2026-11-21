import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.components as PlasmaComponents3
import org.kde.kirigami as Kirigami

import "lib.js" as Lib

// One device: icon, name + type on the left, big light-weight percentage on the right,
// a thin level line underneath. Right-click for options.
Item {
    id: row

    property var device
    property var pal
    property bool muted: false
    property bool pinned: false
    property bool showDivider: true

    signal toggleMute
    signal togglePin
    signal hideDevice

    readonly property real percent: device && device.percent !== null ? device.percent : 0
    readonly property string levelClass: device ? Lib.levelClass(percent, device.charging) : "good"
    readonly property color accent: pal ? pal.levelColor(levelClass) : "#34c759"
    readonly property real vpad: Kirigami.Units.largeSpacing

    function kindLabel(kind) {
        switch (kind) {
        case "mouse": return i18nc("device type", "Mouse");
        case "keyboard": return i18nc("device type", "Keyboard");
        case "headphones": return i18nc("device type", "Headphones");
        case "headset": return i18nc("device type", "Headset");
        case "gamepad": return i18nc("device type", "Controller");
        case "phone": return i18nc("device type", "Phone");
        case "tablet": return i18nc("device type", "Tablet");
        case "touchpad": return i18nc("device type", "Touchpad");
        case "pen": return i18nc("device type", "Pen");
        case "speaker": return i18nc("device type", "Speaker");
        case "laptop": return i18nc("device type", "Laptop");
        case "remote": return i18nc("device type", "Remote");
        default: return i18nc("device type", "Device");
        }
    }

    function transportLabel(t) {
        switch (t) {
        case "bluetooth": return i18nc("connection type", "Bluetooth");
        case "wireless": return i18nc("connection type", "Wireless");
        case "usb": return i18nc("connection type", "USB");
        default: return "";
        }
    }

    readonly property string subtitle: {
        if (!device) {
            return "";
        }
        var parts = [kindLabel(device.kind)];
        // Status replaces the connection type so the line stays short.
        if (device.charging) {
            parts.push(i18n("Charging"));
        } else if (device.state === "full") {
            parts.push(i18n("Full"));
        } else {
            var transport = transportLabel(device.transport);
            if (transport) {
                parts.push(transport);
            }
        }
        return parts.join(" · ");
    }

    implicitHeight: content.implicitHeight + vpad * 2

    ColumnLayout {
        id: content
        anchors {
            left: parent.left
            right: parent.right
            verticalCenter: parent.verticalCenter
            leftMargin: Kirigami.Units.smallSpacing
            rightMargin: Kirigami.Units.smallSpacing
        }
        spacing: Kirigami.Units.largeSpacing

        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.largeSpacing

            Kirigami.Icon {
                source: row.device ? Lib.iconForKind(row.device.kind) : ""
                fallback: "preferences-desktop-peripherals"
                isMask: true
                color: row.pal ? row.pal.ink : Kirigami.Theme.textColor
                Layout.preferredWidth: Math.round(Kirigami.Units.gridUnit * 1.7)
                Layout.preferredHeight: Layout.preferredWidth
                Layout.alignment: Qt.AlignVCenter
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    PlasmaComponents3.Label {
                        Layout.fillWidth: true
                        text: row.device ? row.device.name : ""
                        elide: Text.ElideRight
                        font.pixelSize: Math.round(Kirigami.Units.gridUnit * 0.95)
                        color: row.pal ? row.pal.ink : Kirigami.Theme.textColor
                    }
                    Kirigami.Icon {
                        visible: row.pinned
                        source: "pin"
                        isMask: true
                        color: row.pal ? row.pal.inkSoft : Kirigami.Theme.disabledTextColor
                        Layout.preferredWidth: Kirigami.Units.iconSizes.small
                        Layout.preferredHeight: Kirigami.Units.iconSizes.small
                    }
                    Kirigami.Icon {
                        visible: row.muted
                        source: "notifications-disabled"
                        isMask: true
                        color: row.pal ? row.pal.inkSoft : Kirigami.Theme.disabledTextColor
                        Layout.preferredWidth: Kirigami.Units.iconSizes.small
                        Layout.preferredHeight: Kirigami.Units.iconSizes.small
                    }
                }

                PlasmaComponents3.Label {
                    Layout.fillWidth: true
                    text: row.subtitle
                    elide: Text.ElideRight
                    font.pixelSize: Math.round(Kirigami.Units.gridUnit * 0.75)
                    color: row.pal ? row.pal.inkSoft : Kirigami.Theme.disabledTextColor
                }
            }

            RowLayout {
                spacing: 2
                Layout.alignment: Qt.AlignVCenter
                PlasmaComponents3.Label {
                    Layout.alignment: Qt.AlignBaseline
                    text: row.device ? (row.device.approx ? "~" : "") + Math.round(row.percent) : ""
                    font.pixelSize: Math.round(Kirigami.Units.gridUnit * 2.1)
                    font.weight: Font.Light
                    color: row.levelClass === "low" && row.pal ? row.pal.low : (row.pal ? row.pal.ink : Kirigami.Theme.textColor)
                }
                PlasmaComponents3.Label {
                    Layout.alignment: Qt.AlignBaseline
                    text: "%"
                    font.pixelSize: Math.round(Kirigami.Units.gridUnit * 0.95)
                    color: row.pal ? row.pal.inkSoft : Kirigami.Theme.disabledTextColor
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 4
            radius: 2
            color: row.pal ? row.pal.track : Qt.alpha(Kirigami.Theme.textColor, 0.12)

            Rectangle {
                width: parent.width * Math.max(0, Math.min(1, row.percent / 100))
                height: parent.height
                radius: parent.radius
                color: row.accent
                Behavior on width {
                    NumberAnimation {
                        duration: Kirigami.Units.longDuration
                        easing.type: Easing.OutCubic
                    }
                }
            }
        }
    }

    Rectangle {
        visible: row.showDivider
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        height: 1
        color: row.pal ? row.pal.line : Qt.alpha(Kirigami.Theme.textColor, 0.1)
    }

    TapHandler {
        acceptedButtons: Qt.RightButton
        onTapped: menu.popup()
    }
    TapHandler {
        acceptedButtons: Qt.LeftButton
        longPressThreshold: 0.6
        onLongPressed: menu.popup()
    }

    QQC2.Menu {
        id: menu
        QQC2.MenuItem {
            text: row.pinned ? i18n("Stop showing in panel") : i18n("Show in panel")
            icon.name: "pin"
            onTriggered: row.togglePin()
        }
        QQC2.MenuItem {
            text: row.muted ? i18n("Turn alerts on") : i18n("Mute alerts")
            icon.name: row.muted ? "notifications" : "notifications-disabled"
            onTriggered: row.toggleMute()
        }
        QQC2.MenuItem {
            text: i18n("Hide device")
            icon.name: "view-hidden"
            onTriggered: row.hideDevice()
        }
    }
}
