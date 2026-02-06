// Сервис A-Tracker: создание нового сотрудника (POST).
// Параметры: sFullName, sLoginName, sEmail, sPersNo (из args или args["__json"]).
// Структура по образцу сервиса 6.

try
{
    string sFullName = "";
    string sLoginName = "";
    string sEmail = "";
    string sPersNo = "";
    bool paramsOk = false;

    // 1) Пробуем взять параметры из args (query string / форма)
    if (args != null)
    {
        sFullName = (args.ContainsKey("sFullName") && args["sFullName"] != null ? args["sFullName"] : args.ContainsKey("sfullname") ? args["sfullname"] : null)?.ToString() ?? "";
        sLoginName = (args.ContainsKey("sLoginName") && args["sLoginName"] != null ? args["sLoginName"] : args.ContainsKey("sloginname") ? args["sloginname"] : null)?.ToString() ?? "";
        sEmail = (args.ContainsKey("sEmail") && args["sEmail"] != null ? args["sEmail"] : args.ContainsKey("semail") ? args["semail"] : null)?.ToString() ?? "";
        sPersNo = (args.ContainsKey("sPersNo") && args["sPersNo"] != null ? args["sPersNo"] : args.ContainsKey("spersno") ? args["spersno"] : null)?.ToString() ?? "";
        paramsOk = !string.IsNullOrWhiteSpace(sFullName) || !string.IsNullOrWhiteSpace(sLoginName) || !string.IsNullOrWhiteSpace(sEmail);
    }

    // 2) Если не передан в args — читаем из args["__json"]
    if (!paramsOk && args != null && args.ContainsKey("__json") && args["__json"] != null)
    {
        object j = args["__json"];
        var dict = j as System.Collections.IDictionary;
        if (dict != null)
        {
            sFullName = (dict["sFullName"] ?? dict["SFullName"])?.ToString() ?? "";
            sLoginName = (dict["sLoginName"] ?? dict["SLoginName"])?.ToString() ?? "";
            sEmail = (dict["sEmail"] ?? dict["SEmail"])?.ToString() ?? "";
            sPersNo = (dict["sPersNo"] ?? dict["SPersNo"])?.ToString() ?? "";
            paramsOk = !string.IsNullOrWhiteSpace(sFullName) || !string.IsNullOrWhiteSpace(sLoginName) || !string.IsNullOrWhiteSpace(sEmail);
        }
        else
        {
            var body = j != null ? j.ToString() : null;
            if (!string.IsNullOrWhiteSpace(body) && (body.TrimStart().StartsWith("{") || body.TrimStart().StartsWith("[")))
            {
                var doc = System.Text.Json.JsonDocument.Parse(body);
                var root = doc.RootElement;
                sFullName = root.TryGetProperty("sFullName", out var p) ? (p.GetString() ?? "") : (root.TryGetProperty("SFullName", out p) ? (p.GetString() ?? "") : "");
                sLoginName = root.TryGetProperty("sLoginName", out p) ? (p.GetString() ?? "") : (root.TryGetProperty("SLoginName", out p) ? (p.GetString() ?? "") : "");
                sEmail = root.TryGetProperty("sEmail", out p) ? (p.GetString() ?? "") : (root.TryGetProperty("SEmail", out p) ? (p.GetString() ?? "") : "");
                sPersNo = root.TryGetProperty("sPersNo", out p) ? (p.GetString() ?? "") : (root.TryGetProperty("SPersNo", out p) ? (p.GetString() ?? "") : "");
                paramsOk = !string.IsNullOrWhiteSpace(sFullName) || !string.IsNullOrWhiteSpace(sLoginName) || !string.IsNullOrWhiteSpace(sEmail);
            }
        }
    }

    if (!paramsOk || (string.IsNullOrEmpty(sFullName) && string.IsNullOrEmpty(sLoginName) && string.IsNullOrEmpty(sEmail)))
    {
        returnCode = "Error";
        message = "Укажите ФИО, логин или почту";
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    }
    else
    {
        var tableName = "itamEmplDept";
        var fields = new System.Collections.Generic.List<string>() { "ID", "sFullName", "sLoginName", "sEmail", "sPersNo" };
        // Пустой результат (1=0) — создаём новую запись
        var empl = new itamDataObject(tableName, fields: fields, where: "1=0", parameters: new System.Collections.Generic.Dictionary<string, object>());

        if (empl.Rows == null)
            empl.Rows = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();

        var newRow = new System.Collections.Generic.Dictionary<string, object>()
        {
            ["sFullName"] = string.IsNullOrEmpty(sFullName) ? "" : sFullName.Trim(),
            ["sLoginName"] = string.IsNullOrEmpty(sLoginName) ? "" : sLoginName.Trim(),
            ["sEmail"] = string.IsNullOrEmpty(sEmail) ? "" : sEmail.Trim(),
            ["sPersNo"] = string.IsNullOrEmpty(sPersNo) ? "" : sPersNo.Trim()
        };
        empl.Rows.Add(newRow);
        empl.Insert();

        // ID новой записи — если платформа заполняет его после Insert
        var newId = empl.Rows.Count > 0 && empl.Rows[empl.Rows.Count - 1].ContainsKey("ID") ? empl.Rows[empl.Rows.Count - 1]["ID"] : null;
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>
        {
            new System.Collections.Generic.Dictionary<string, object> { ["ID"] = newId ?? 0 }
        };
        returnCode = "Success";
        message = "Сотрудник создан";
    }
}
catch (System.Exception ex)
{
    returnCode = "Error";
    message = ex.ToString();
    data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
}
