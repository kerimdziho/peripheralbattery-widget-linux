import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    id: page

    property string cfg_appearance
    property string cfg_panelDisplay
    property alias cfg_pollSeconds: pollSpin.value
    property alias cfg_includeSystemBattery: includeSystem.checked
    property alias cfg_demoMode: demoMode.checked
    property var cfg_hiddenDevices: []
    property var cfg_mutedDevices: []
    property string cfg_panelDevice

    Kirigami.FormLayout {
        QQC2.ComboBox {
            id: appearanceBox
            Kirigami.FormData.label: i18n("Appearance:")
            textRole: "text"
            valueRole: "value"
            model: [
                { text: i18n("Follow system theme"), value: "system" },
                { text: i18n("Light"), value: "light" },
                { text: i18n("Dark"), value: "dark" }
            ]
            currentIndex: Math.max(0, indexOfValue(page.cfg_appearance))
            onActivated: page.cfg_appearance = currentValue
        }

        QQC2.ComboBox {
            id: displayBox
            Kirigami.FormData.label: i18n("In the panel show:")
            textRole: "text"
            valueRole: "value"
            model: [
                { text: i18n("Percentage, e.g. 38%"), value: "text" },
                { text: i18n("Icon and percentage"), value: "iconText" },
                { text: i18n("Icon only"), value: "icon" }
            ]
            currentIndex: Math.max(0, indexOfValue(page.cfg_panelDisplay))
            onActivated: page.cfg_panelDisplay = currentValue
        }
        QQC2.Label {
            Layout.maximumWidth: Kirigami.Units.gridUnit * 22
            wrapMode: Text.WordWrap
            opacity: 0.7
            font: Kirigami.Theme.smallFont
            text: i18n("The panel shows your weakest device. Right-click a device in the list and choose “Show in panel” to pick another one.")
        }

        Item {
            Kirigami.FormData.isSection: true
        }

        QQC2.SpinBox {
            id: pollSpin
            Kirigami.FormData.label: i18n("Refresh every:")
            from: 15
            to: 3600
            stepSize: 15
            editable: true
            textFromValue: function (value) {
                return i18np("%1 second", "%1 seconds", value);
            }
            valueFromText: function (text) {
                var n = parseInt(text, 10);
                return isNaN(n) ? 60 : n;
            }
        }

        QQC2.CheckBox {
            id: includeSystem
            Kirigami.FormData.label: i18n("Devices:")
            text: i18n("Also show this computer's own battery")
        }

        QQC2.CheckBox {
            id: demoMode
            text: i18n("Show demo devices (to try the widget)")
        }

        Item {
            Kirigami.FormData.isSection: true
        }

        RowLayout {
            Kirigami.FormData.label: i18n("Reset:")
            QQC2.Button {
                text: i18n("Unhide devices")
                enabled: page.cfg_hiddenDevices.length > 0
                onClicked: page.cfg_hiddenDevices = []
            }
            QQC2.Button {
                text: i18n("Unmute all")
                enabled: page.cfg_mutedDevices.length > 0
                onClicked: page.cfg_mutedDevices = []
            }
            QQC2.Button {
                text: i18n("Unpin")
                enabled: page.cfg_panelDevice !== ""
                onClicked: page.cfg_panelDevice = ""
            }
        }
    }
}
