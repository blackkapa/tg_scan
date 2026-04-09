    // Сервис A-Tracker (POST): CreateAssetAddRequest
    // Назначение: создать заявку на добавление техники в itamRequest.
    // Вход (args или __json):
    // - requesterEmployeeId (int, желательно)
    // - requesterFio (string)
    // - requesterEmail (string)
    // - requesterLogin (string)
    // - assetName (string, обяз.)
    // - categoryName (string, обяз.)
    // - serialNo (string)
    // - inventoryNo (string)
    // - comment (string)
    // - seType (int, default=1)
    // - statusOnCreate (int, default=7)
    //
    // Выход:
    // data[0] = { requestId, reqNumber, status, resolvedUserId, resolvedUserFio, resolveMode, resolveNote }

    try
    {
        string S(object o) { return o == null ? "" : o.ToString().Trim(); }
        long L(object o) { long x; return long.TryParse(S(o), out x) ? x : 0; }

    object J = null;
    if (args != null && args.ContainsKey("__json")) J = args["__json"];

    System.Collections.IDictionary d = J as System.Collections.IDictionary;
    if (d == null) d = args as System.Collections.IDictionary;
    if (d == null) d = new System.Collections.Hashtable();

    // Универсальный reader:
    // 1) ключ как есть
    // 2) регистронезависимый поиск по IDictionary
    object V(string key)
    {
        if (d.Contains(key)) return d[key];
        foreach (System.Collections.DictionaryEntry de in d)
        {
            if (de.Key != null && string.Equals(de.Key.ToString(), key, System.StringComparison.OrdinalIgnoreCase))
                return de.Value;
        }
        return null;
    }

    // Фолбэк: если JSON пришёл строкой, распарсим вручную.
    System.Collections.Generic.Dictionary<string, object> jdict =
        new System.Collections.Generic.Dictionary<string, object>(System.StringComparer.OrdinalIgnoreCase);
    try
    {
        string raw = S(J);
        if (string.IsNullOrWhiteSpace(raw) && args != null && args.ContainsKey("body"))
            raw = S(args["body"]);
        if (!string.IsNullOrWhiteSpace(raw) && raw.TrimStart().StartsWith("{"))
        {
            var doc = System.Text.Json.JsonDocument.Parse(raw);
            foreach (var p in doc.RootElement.EnumerateObject())
            {
                if (p.Value.ValueKind == System.Text.Json.JsonValueKind.String) jdict[p.Name] = p.Value.GetString();
                else if (p.Value.ValueKind == System.Text.Json.JsonValueKind.Number)
                {
                    long n;
                    if (p.Value.TryGetInt64(out n)) jdict[p.Name] = n;
                    else jdict[p.Name] = p.Value.ToString();
                }
                else if (p.Value.ValueKind == System.Text.Json.JsonValueKind.True || p.Value.ValueKind == System.Text.Json.JsonValueKind.False)
                    jdict[p.Name] = p.Value.GetBoolean();
                else
                    jdict[p.Name] = p.Value.ToString();
            }
        }
    }
    catch { }

    object VV(params string[] keys)
    {
        foreach (var k in keys)
        {
            var v = V(k);
            if (v != null && !string.IsNullOrWhiteSpace(S(v))) return v;
            if (jdict.ContainsKey(k) && jdict[k] != null && !string.IsNullOrWhiteSpace(S(jdict[k]))) return jdict[k];
        }
        return null;
    }

    var requesterEmployeeId = L(VV("requesterEmployeeId", "RequesterEmployeeId", "lRequesterId", "LRequesterId"));
    var requesterFio = S(VV("requesterFio", "RequesterFio", "fio", "Fio"));
    var requesterEmail = S(VV("requesterEmail", "RequesterEmail", "email", "Email")).ToLower();
    var requesterLogin = S(VV("requesterLogin", "RequesterLogin", "login", "Login")).ToLower();
    var assetName = S(VV("assetName", "AssetName", "name", "Name", "sFullName"));
    var categoryName = S(VV("categoryName", "CategoryName", "category", "Category"));
    var serialNo = S(VV("serialNo", "SerialNo", "serial", "Serial"));
    var inventoryNo = S(VV("inventoryNo", "InventoryNo", "inventNumber", "InventNumber", "inventory", "Inventory"));
    var comment = S(VV("comment", "Comment", "sComment"));
    var seType = (int)(L(VV("seType", "SeType")) > 0 ? L(VV("seType", "SeType")) : 1);
    var statusOnCreate = (int)(L(VV("statusOnCreate", "StatusOnCreate")) > 0 ? L(VV("statusOnCreate", "StatusOnCreate")) : 7);

    if (string.IsNullOrWhiteSpace(assetName) || string.IsNullOrWhiteSpace(categoryName))
        {
            returnCode = "Error";
            message = "assetName и categoryName обязательны.";
            data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
        }
    else
    {
        // ResolveUser: employeeId -> email -> login -> fio (только однозначный match).
        long resolvedUserId = 0;
        string resolvedUserFio = "";
        string resolveMode = "none";
        string resolveNote = "";

        if (requesterEmployeeId > 0)
            {
            var byId = new itamDataObject("itamEmplDept", "[ID] = " + requesterEmployeeId);
            if (byId.Rows != null && byId.Rows.Count == 1)
            {
                resolvedUserId = requesterEmployeeId;
                resolvedUserFio = byId.Rows[0].ContainsKey("sFullName") && byId.Rows[0]["sFullName"] != null
                    ? byId.Rows[0]["sFullName"].ToString()
                    : "";
                resolveMode = "employeeId";
            }
            }

        System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>> ResolveByWhere(string where)
        {
            var obj = new itamDataObject("itamEmplDept", where);
            return obj.Rows ?? new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
        }

        if (resolvedUserId == 0 && !string.IsNullOrWhiteSpace(requesterEmail))
        {
            var rows = ResolveByWhere("[sEmail] = '" + requesterEmail.Replace("'", "''") + "'");
            if (rows.Count == 1)
            {
                resolvedUserId = L(rows[0]["ID"]);
                resolvedUserFio = rows[0].ContainsKey("sFullName") && rows[0]["sFullName"] != null ? rows[0]["sFullName"].ToString() : "";
                resolveMode = "email";
            }
            else if (rows.Count > 1)
            {
                resolveMode = "ambiguous";
                resolveNote = "Найдено несколько сотрудников по email.";
            }
        }
        if (resolvedUserId == 0 && resolveMode != "ambiguous" && !string.IsNullOrWhiteSpace(requesterLogin))
        {
            var rows = ResolveByWhere("[sLoginName] = '" + requesterLogin.Replace("'", "''") + "'");
            if (rows.Count == 1)
            {
                resolvedUserId = L(rows[0]["ID"]);
                resolvedUserFio = rows[0].ContainsKey("sFullName") && rows[0]["sFullName"] != null ? rows[0]["sFullName"].ToString() : "";
                resolveMode = "login";
            }
            else if (rows.Count > 1)
            {
                resolveMode = "ambiguous";
                resolveNote = "Найдено несколько сотрудников по логину.";
            }
        }
        if (resolvedUserId == 0 && resolveMode != "ambiguous" && !string.IsNullOrWhiteSpace(requesterFio))
        {
            var rows = ResolveByWhere("[sFullName] = '" + requesterFio.Replace("'", "''") + "'");
            if (rows.Count == 1)
            {
                resolvedUserId = L(rows[0]["ID"]);
                resolvedUserFio = rows[0].ContainsKey("sFullName") && rows[0]["sFullName"] != null ? rows[0]["sFullName"].ToString() : "";
                resolveMode = "fio";
            }
            else if (rows.Count > 1)
            {
                resolveMode = "ambiguous";
                resolveNote = "Найдено несколько сотрудников по ФИО.";
            }
        }

        var req = new itamDataObject("itamRequest", 1);
        req.Rows[0]["seType"] = seType;
        req.Rows[0]["seStatus"] = statusOnCreate;
        if (requesterEmployeeId > 0) req.Rows[0]["lRequesterId"] = (int)requesterEmployeeId;
        req.Rows[0]["sFullName"] = "Заявка на добавление техники";
        req.Rows[0]["sPurchaseItem"] = categoryName + " / " + assetName;
        req.Rows[0]["sPurpose"] =
            "Категория: " + categoryName + "\n" +
            "Наименование: " + assetName + "\n" +
            "Серийный: " + serialNo + "\n" +
            "Инвентарный: " + inventoryNo + "\n" +
            "Комментарий: " + comment + "\n" +
            "Инициатор: " + requesterFio + " / " + requesterEmail + " / " + requesterLogin + "\n" +
            "ResolveUser: mode=" + resolveMode + ", userId=" + resolvedUserId + ", note=" + resolveNote;

        // Опционально сохраняем служебные поля, если они есть в модели:
        // req.Rows[0]["lResolvedUserId"] = resolvedUserId > 0 ? (object)(int)resolvedUserId : null;
        // req.Rows[0]["sResolveMode"] = resolveMode;
        // req.Rows[0]["sResolveNote"] = resolveNote;

        req.Insert();
        int requestId = (int)req.Rows[0]["ID"];
        string reqNumber = req.Rows[0].ContainsKey("sReqNumber") && req.Rows[0]["sReqNumber"] != null
            ? req.Rows[0]["sReqNumber"].ToString()
            : "";

        returnCode = "Success";
        message = "";
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>
        {
            new System.Collections.Generic.Dictionary<string, object>
            {
                { "requestId", requestId },
                { "reqNumber", reqNumber },
                { "status", statusOnCreate },
                { "resolvedUserId", resolvedUserId },
                { "resolvedUserFio", resolvedUserFio },
                { "resolveMode", resolveMode },
                { "resolveNote", resolveNote }
            }
        };
    }
    }
    catch (System.Exception ex)
    {
        returnCode = "Error";
        message = ex.ToString();
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    }
