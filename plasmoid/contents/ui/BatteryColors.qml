import QtQuick
import org.kde.kirigami as Kirigami

// Colours for the widget.
//   system - follows the active Plasma colour scheme (light or dark with your theme)
//   light  - fixed minimal light palette
//   dark   - fixed minimal dark palette
Item {
    id: pal

    property string mode: "system"

    readonly property bool forced: mode === "light" || mode === "dark"
    readonly property bool dark: mode === "dark" || (mode !== "light" && luma(Kirigami.Theme.backgroundColor) < 0.5)

    readonly property color card: forced ? (dark ? "#1c1c1e" : "#ffffff") : Kirigami.Theme.backgroundColor
    readonly property color ink: forced ? (dark ? "#f2f2f7" : "#111111") : Kirigami.Theme.textColor
    readonly property color inkSoft: forced ? (dark ? "#8e8e93" : "#8a8a8e") : Qt.alpha(Kirigami.Theme.textColor, 0.6)
    readonly property color track: forced ? (dark ? "#2c2c2e" : "#ececf0") : Qt.alpha(Kirigami.Theme.textColor, 0.12)
    readonly property color line: forced ? (dark ? "#2c2c2e" : "#ececf0") : Qt.alpha(Kirigami.Theme.textColor, 0.1)

    readonly property color good: dark ? "#30d158" : "#34c759"
    readonly property color medium: dark ? "#ff9f0a" : "#ff9500"
    readonly property color low: dark ? "#ff453a" : "#ff3b30"
    readonly property color charging: dark ? "#0a84ff" : "#007aff"

    visible: false
    width: 0
    height: 0

    function luma(c) {
        return 0.299 * c.r + 0.587 * c.g + 0.114 * c.b;
    }

    // cls: "good" | "medium" | "low" | "charging"
    function levelColor(cls) {
        switch (cls) {
        case "low": return low;
        case "medium": return medium;
        case "charging": return charging;
        default: return good;
        }
    }
}
