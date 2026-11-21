import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents3
import org.kde.kirigami as Kirigami

import "lib.js" as Lib

// Panel / taskbar view. Default: just the number, e.g. "38%".
MouseArea {
    id: compact

    property var device: null          // the device shown (pinned one, else the lowest)
    property bool expanded: false
    property string display: "text"    // text | iconText | icon
    signal activated

    readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical
    readonly property bool showText: device !== null && display !== "icon"
    readonly property bool showIcon: display !== "text" || device === null
    readonly property real thickness: vertical ? width : height
    property bool wasExpanded: false

    readonly property color textColor: {
        if (device && device.charging) {
            return Kirigami.Theme.highlightColor;
        }
        if (device && device.percent <= 15) {
            return Kirigami.Theme.negativeTextColor;
        }
        return Kirigami.Theme.textColor;
    }

    hoverEnabled: true
    acceptedButtons: Qt.LeftButton
    onPressed: wasExpanded = expanded
    onClicked: if (wasExpanded === expanded) activated()

    Layout.minimumWidth: vertical ? 0 : content.implicitWidth + Kirigami.Units.smallSpacing * 2
    Layout.minimumHeight: vertical ? content.implicitHeight : 0
    Layout.preferredWidth: Layout.minimumWidth
    Layout.preferredHeight: Layout.minimumHeight

    GridLayout {
        id: content
        anchors.centerIn: parent
        columns: compact.vertical ? 1 : 2
        rowSpacing: 0
        columnSpacing: Kirigami.Units.smallSpacing

        Kirigami.Icon {
            visible: compact.showIcon
            Layout.alignment: Qt.AlignCenter
            Layout.preferredWidth: Math.max(Kirigami.Units.iconSizes.small, Math.round(compact.thickness * 0.8))
            Layout.preferredHeight: Layout.preferredWidth
            source: compact.device ? Lib.batteryIcon(compact.device.percent, compact.device.charging) : "preferences-desktop-peripherals"
            fallback: "battery"
            active: compact.containsMouse
        }

        PlasmaComponents3.Label {
            visible: compact.showText
            Layout.alignment: Qt.AlignCenter
            text: compact.device ? Math.round(compact.device.percent) + "%" : ""
            color: compact.textColor
            font.weight: Font.Medium
            font.pixelSize: compact.vertical
                ? Math.max(9, Math.round(compact.width * 0.38))
                : Math.max(10, Math.round(compact.height * 0.5))
        }
    }
}
