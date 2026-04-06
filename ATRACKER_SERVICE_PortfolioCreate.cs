// Сервис A-Tracker (POST): PortfolioCreate
// Назначение: создать карточку актива itamPortfolio из веб-админки.
// Вход (args или __json), поддерживаются ключи в разных регистрах:
// - assetName | AssetName | sFullName
// - lUserId | LUserId (обяз.)
// - categoryId | CategoryId | lCategoryId | LCategoryId
// - serialNo | SerialNo | sSerialNo | SSerialNo
// - inventNumber | InventNumber | sInventNumber | SInventNumber
// - comment | Comment | sComment | SComment
// - locationId | LocationId | lLocationId | LLocationId (опц.)
//
// Выход:
// data[0] = { ID, assetId, portfolioId, sFullName }

try
{
    string S(object o) { return o == null ? "" : o.ToString().Trim(); }
    long L(object o) { long x; return long.TryParse(S(o), out x) ? x : 0; }

    object J = null;
    if (args != null && args.ContainsKey("__json")) J = args["__json"];

    System.Collections.IDictionary d = J as System.Collections.IDictionary;
    if (d == null) d = args as System.Collections.IDictionary;
    if (d == null) d = new System.Collections.Hashtable();

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

    string assetName = S(VV("assetName", "AssetName", "sFullName", "SFullName", "name", "Name"));
    long lUserId = L(VV("lUserId", "LUserId"));
    long categoryId = L(VV("categoryId", "CategoryId", "lCategoryId", "LCategoryId"));
    string serialNo = S(VV("serialNo", "SerialNo", "sSerialNo", "SSerialNo"));
    string inventNumber = S(VV("inventNumber", "InventNumber", "sInventNumber", "SInventNumber", "sInventoryNo", "SInventoryNo"));
    string comment = S(VV("comment", "Comment", "sComment", "SComment"));
    long locationId = L(VV("locationId", "LocationId", "lLocationId", "LLocationId", "lt_lLocationId", "Lt_lLocationId"));

    if (string.IsNullOrWhiteSpace(assetName))
    {
        returnCode = "Error";
        message = "assetName (sFullName) обязателен.";
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    }
    else if (lUserId <= 0)
    {
        returnCode = "Error";
        message = "lUserId обязателен.";
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    }
    else
    {
        int newId = 0;
        System.Exception lastEx = null;

        // 1) Базовая попытка: ссылочные поля как int (обычно это корректно для Validate).
        // 2) Фолбэк: если workflow падает на Int32->String, повторяем с string.
        foreach (bool idsAsString in new[] { false, true })
        {
            try
            {
                var p = new itamDataObject("itamPortfolio", 1);
                p.Rows[0]["sFullName"] = assetName;
                p.Rows[0]["lUserId"] = idsAsString ? (object)S(lUserId) : (object)((int)lUserId);

                if (categoryId > 0)
                {
                    try { p.Rows[0]["lCategoryId"] = idsAsString ? (object)S(categoryId) : (object)((int)categoryId); } catch { }
                }
                if (locationId > 0)
                {
                    try { p.Rows[0]["lLocationId"] = idsAsString ? (object)S(locationId) : (object)((int)locationId); } catch { }
                }
                if (!string.IsNullOrWhiteSpace(serialNo))
                    p.Rows[0]["sSerialNo"] = serialNo;
                if (!string.IsNullOrWhiteSpace(inventNumber))
                {
                    if (p.Rows[0].ContainsKey("sInventNumber")) p.Rows[0]["sInventNumber"] = inventNumber;
                    else p.Rows[0]["sInventoryNo"] = inventNumber;
                }
                if (!string.IsNullOrWhiteSpace(comment))
                    p.Rows[0]["sComment"] = comment;

                p.Insert();
                newId = (int)L(p.Rows[0]["ID"]);
                if (newId > 0) break;
            }
            catch (System.Exception exTry)
            {
                lastEx = exTry;
                // На первой итерации пробуем фолбэк только при известной несовместимости.
                var msg = (exTry == null ? "" : exTry.ToString());
                if (!idsAsString && msg.IndexOf("Int32", System.StringComparison.OrdinalIgnoreCase) >= 0
                    && msg.IndexOf("String", System.StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    continue;
                }
                throw;
            }
        }
        if (newId <= 0 && lastEx != null) throw lastEx;
        if (newId <= 0) throw new Exception("A-Tracker не вернул ID созданного актива.");

        returnCode = "Success";
        message = "";
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>
        {
            new System.Collections.Generic.Dictionary<string, object>
            {
                { "ID", newId },
                { "assetId", newId },
                { "portfolioId", newId },
                { "sFullName", assetName }
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

