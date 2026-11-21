import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM
import org.kde.plasma.plasma5support as Plasma5Support

import "lib.js" as Lib

KCM.SimpleKCM {
    id: page

    property alias cfg_sendNotifications: notifyEnabled.checked
    property string cfg_thresholds
    property alias cfg_criticalBelow: criticalSpin.value
    property alias cfg_chargedEnabled: chargedEnabled.checked
    property alias cfg_chargedThreshold: chargedSpin.value
    property alias cfg_persistent: persistent.checked
    property alias cfg_criticalSticky: criticalSticky.checked
    property alias cfg_playSound: sound.checked
    property alias cfg_ntfyEnabled: ntfyEnabled.checked
    property alias cfg_ntfyServer: ntfyServer.text
    property alias cfg_ntfyTopic: ntfyTopic.text
    property alias cfg_ntfyToken: ntfyToken.text

    // Stages offered as toggles. Any other value can still be set through the
    // config file (see README) - those are preserved here.
    readonly property var stageChoices: [90, 80, 75, 70, 60, 50, 40, 30, 25, 20, 15, 10, 5, 3]

    function hasStage(v) {
        return Lib.parseList(cfg_thresholds).indexOf(v) >= 0;
    }

    function setStage(v, on) {
        var list = Lib.parseList(cfg_thresholds).filter(function (x) {
            return x !== v;
        });
        if (on) {
            list.push(v);
        }
        list.sort(function (a, b) {
            return b - a;
        });
        cfg_thresholds = list.join(",");
    }

    function currentSettings() {
        return Lib.buildSettings({
            pollSeconds: 60,
            thresholds: cfg_thresholds,
            criticalBelow: cfg_criticalBelow,
            chargedEnabled: cfg_chargedEnabled,
            chargedThreshold: cfg_chargedThreshold,
            hysteresis: 3,
            includeSystemBattery: false,
            demoMode: false,
            useBluez: true,
            useHeadsetcontrol: true,
            mutedDevices: [],
            hiddenDevices: [],
            sendNotifications: true,
            persistent: cfg_persistent,
            criticalSticky: cfg_criticalSticky,
            playSound: cfg_playSound,
            ntfyEnabled: cfg_ntfyEnabled,
            ntfyServer: cfg_ntfyServer,
            ntfyTopic: cfg_ntfyTopic,
            ntfyToken: cfg_ntfyToken
        });
    }

    readonly property string scriptPath: Lib.urlToPath(Qt.resolvedUrl("../code/pbattery.py"))

    function runTest(extraArgs) {
        testResult.text = i18n("Sending…");
        tester.connectSource(Lib.backendCommand(scriptPath, "test-notify", currentSettings(), extraArgs + " --level " + (extraArgs.indexOf("--charged") >= 0 ? "100" : "8")));
    }

    Plasma5Support.DataSource {
        id: tester
        engine: "executable"
        connectedSources: []
        interval: 0
        onNewData: function (sourceName, data) {
            disconnectSource(sourceName);
            if (data["exit code"] === 0) {
                testResult.text = i18n("Test notification sent.");
            } else {
                var err = String(data["stderr"] || data["stdout"] || "").trim();
                testResult.text = i18n("Could not send: %1", err.length > 0 ? err : i18n("unknown error"));
            }
        }
    }

    Kirigami.FormLayout {
        QQC2.CheckBox {
            id: notifyEnabled
            Kirigami.FormData.label: i18n("Alerts:")
            text: i18n("Send battery notifications from this widget")
        }
        QQC2.Label {
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
            wrapMode: Text.WordWrap
            opacity: 0.7
            font: Kirigami.Theme.smallFont
            text: i18n("Turn this off if you run the background service instead. Several widgets never send the same alert twice.")
        }

        Item {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: i18n("When the battery is running down")
        }

        Flow {
            Kirigami.FormData.label: i18n("Notify me at:")
            Kirigami.FormData.labelAlignment: Qt.AlignTop
            Layout.preferredWidth: Kirigami.Units.gridUnit * 22
            Layout.maximumWidth: Kirigami.Units.gridUnit * 22
            spacing: Kirigami.Units.smallSpacing
            enabled: notifyEnabled.checked

            Repeater {
                model: page.stageChoices
                delegate: QQC2.CheckBox {
                    required property int modelData
                    text: modelData + "%"
                    checked: page.hasStage(modelData)
                    onToggled: page.setStage(modelData, checked)
                }
            }
        }
        QQC2.Label {
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
            wrapMode: Text.WordWrap
            opacity: 0.7
            font: Kirigami.Theme.smallFont
            text: i18n("You are told once when a device drops to each ticked level. Jumping over several levels sends a single alert.")
        }

        QQC2.SpinBox {
            id: criticalSpin
            Kirigami.FormData.label: i18n("Urgent alert at or below:")
            from: 0
            to: 99
            textFromValue: function (value) {
                return value === 0 ? i18n("Never") : value + "%";
            }
            valueFromText: function (text) {
                var n = parseInt(text, 10);
                return isNaN(n) ? 0 : n;
            }
        }

        QQC2.CheckBox {
            id: criticalSticky
            text: i18n("Keep urgent alerts on screen until dismissed")
        }

        Item {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: i18n("When charging")
        }

        RowLayout {
            Kirigami.FormData.label: i18n("Charged alert:")
            QQC2.CheckBox {
                id: chargedEnabled
                text: i18n("Tell me when a device reaches")
            }
            QQC2.SpinBox {
                id: chargedSpin
                enabled: chargedEnabled.checked
                from: 50
                to: 100
                textFromValue: function (value) {
                    return value + "%";
                }
                valueFromText: function (text) {
                    var n = parseInt(text, 10);
                    return isNaN(n) ? 100 : n;
                }
            }
        }

        Item {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: i18n("How to notify")
        }

        QQC2.CheckBox {
            id: persistent
            Kirigami.FormData.label: i18n("Desktop:")
            text: i18n("Keep every alert on screen until dismissed")
        }
        QQC2.CheckBox {
            id: sound
            text: i18n("Play the notification sound")
        }

        Item {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: i18n("Push notifications to your phone")
        }

        QQC2.CheckBox {
            id: ntfyEnabled
            Kirigami.FormData.label: i18n("ntfy:")
            text: i18n("Also send alerts through ntfy")
        }
        QQC2.TextField {
            id: ntfyServer
            Kirigami.FormData.label: i18n("Server:")
            Layout.preferredWidth: Kirigami.Units.gridUnit * 16
            enabled: ntfyEnabled.checked
            placeholderText: "https://ntfy.sh"
        }
        QQC2.TextField {
            id: ntfyTopic
            Kirigami.FormData.label: i18n("Topic:")
            Layout.preferredWidth: Kirigami.Units.gridUnit * 16
            enabled: ntfyEnabled.checked
            placeholderText: i18n("a long, hard-to-guess name")
        }
        QQC2.TextField {
            id: ntfyToken
            Kirigami.FormData.label: i18n("Access token:")
            Layout.preferredWidth: Kirigami.Units.gridUnit * 16
            enabled: ntfyEnabled.checked
            echoMode: TextInput.Password
            placeholderText: i18n("optional")
        }
        QQC2.Label {
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
            wrapMode: Text.WordWrap
            opacity: 0.7
            font: Kirigami.Theme.smallFont
            text: i18n("Install the free ntfy app on your phone and subscribe to the same topic. Device names and levels are sent to the server; anyone who knows the topic can read them. The token is stored unencrypted in your Plasma config.")
        }

        Item {
            Kirigami.FormData.isSection: true
        }

        RowLayout {
            Kirigami.FormData.label: i18n("Try it:")
            QQC2.Button {
                icon.name: "dialog-warning"
                text: i18n("Test low battery")
                onClicked: page.runTest("--critical")
            }
            QQC2.Button {
                icon.name: "battery-100-charging"
                text: i18n("Test charged")
                onClicked: page.runTest("--charged")
            }
        }
        QQC2.Label {
            id: testResult
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
            wrapMode: Text.WordWrap
            visible: text !== ""
        }
    }
}
