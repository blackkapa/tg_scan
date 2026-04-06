// Мастер A-Tracker: финальный transitionScript для Action/Wizard.
// Формат скрипта адаптирован под руководство Action.md:
// - входные значения берем через wizard.GetValue("SQLName");
// - контекст берем через wizard.context["FieldName"].
//
// Требуемые SQLName в мастере:
// requestId, mode, lUserId, lLocationId, assetName, categoryId,
// serialNo, inventNumber, assetComment, chosenPortfolioId, updateUser, updateLocation.
//
// mode: create | existing

try
{
    string S(object o) { return o == null ? "" : o.ToString().Trim(); }
    long L(object o) { long x; return long.TryParse(S(o), out x) ? x : 0; }
    bool B(object o, bool defVal)
    {
        var s = S(o).ToLower();
        if (s == "1" || s == "true" || s == "yes" || s == "on") return true;
        if (s == "0" || s == "false" || s == "no" || s == "off") return false;
        return defVal;
    }

    long requestId = 0;
    try { requestId = L(wizard.GetValue("requestId")); } catch { requestId = 0; }
    if (requestId <= 0 && wizard.context != null && wizard.context.ContainsKey("ID"))
        requestId = L(wizard.context["ID"]);
    if (requestId <= 0 && wizard.context != null && wizard.context.ContainsKey("requestId"))
        requestId = L(wizard.context["requestId"]);

    string modeRaw = "";
    try { modeRaw = S(wizard.GetValue("mode")); } catch { modeRaw = ""; }
    if (string.IsNullOrWhiteSpace(modeRaw)) modeRaw = "1";
    string mode = (modeRaw == "2") ? "existing" : "create";

    long lUserId = 0; try { lUserId = L(wizard.GetValue("lUserId")); } catch { lUserId = 0; }
    long lLocationId = 0; try { lLocationId = L(wizard.GetValue("lLocationId")); } catch { lLocationId = 0; }
    string assetName = ""; try { assetName = S(wizard.GetValue("assetName")); } catch { assetName = ""; }
    long categoryId = 0; try { categoryId = L(wizard.GetValue("categoryId")); } catch { categoryId = 0; }
    string serialNo = ""; try { serialNo = S(wizard.GetValue("serialNo")); } catch { serialNo = ""; }
    string inventNumber = ""; try { inventNumber = S(wizard.GetValue("inventNumber")); } catch { inventNumber = ""; }
    string assetComment = ""; try { assetComment = S(wizard.GetValue("assetComment")); } catch { assetComment = ""; }
    long chosenPortfolioId = 0; try { chosenPortfolioId = L(wizard.GetValue("chosenPortfolioId")); } catch { chosenPortfolioId = 0; }
    bool updateUser = true; try { updateUser = B(wizard.GetValue("updateUser"), true); } catch { updateUser = true; }
    bool updateLocation = true; try { updateLocation = B(wizard.GetValue("updateLocation"), true); } catch { updateLocation = true; }

    if (requestId <= 0) throw new Exception("Не передан requestId (ID заявки).");
    if (lUserId <= 0) throw new Exception("Не выбран пользователь актива (lUserId).");
    if (!(modeRaw == "1" || modeRaw == "2")) throw new Exception("Некорректный mode. Ожидается 1/2.");

    var reqObj = new itamDataObject("itamRequest", "[ID] = " + requestId);
    if (reqObj.Rows == null || reqObj.Rows.Count == 0)
        throw new Exception("Заявка itamRequest не найдена: ID=" + requestId);

    var req = reqObj.Rows[0];
    var reqStatus = (int)L(req.ContainsKey("seStatus") ? req["seStatus"] : null);
    if (reqStatus != 7)
        throw new Exception("Заявка не в статусе 7. Текущий статус: " + reqStatus);

    string reqNumber = req.ContainsKey("sReqNumber") && req["sReqNumber"] != null
        ? req["sReqNumber"].ToString().Trim()
        : "";
    string reqTag = "[REQ] " + reqNumber;
    int finalAssetId = 0;

    if (mode == "create")
    {
        if (string.IsNullOrWhiteSpace(assetName))
            throw new Exception("Для режима create заполните assetName.");

        var p = new itamDataObject("itamPortfolio", 1);
        p.Rows[0]["sFullName"] = assetName;
        p.Rows[0]["lUserId"] = (int)lUserId;
        if (lLocationId > 0)
        {
            // В разных внедрениях поле может называться по-разному.
            if (p.Rows[0].ContainsKey("lt_lLocationId")) p.Rows[0]["lt_lLocationId"] = (int)lLocationId;
            else if (p.Rows[0].ContainsKey("lLocationId")) p.Rows[0]["lLocationId"] = (int)lLocationId;
            else p.Rows[0]["lt_lLocationId"] = (int)lLocationId;
        }
        if (categoryId > 0)
        {
            if (p.Rows[0].ContainsKey("lt_lCategoryId")) p.Rows[0]["lt_lCategoryId"] = (int)categoryId;
            else if (p.Rows[0].ContainsKey("lCategoryId")) p.Rows[0]["lCategoryId"] = (int)categoryId;
        }
        if (!string.IsNullOrWhiteSpace(serialNo)) p.Rows[0]["sSerialNo"] = serialNo;
        if (!string.IsNullOrWhiteSpace(inventNumber))
        {
            if (p.Rows[0].ContainsKey("sInventNumber")) p.Rows[0]["sInventNumber"] = inventNumber;
            else p.Rows[0]["sInventoryNo"] = inventNumber;
        }

        // Комментарий с [REQ] без дубля
        string c = assetComment;
        if (string.IsNullOrWhiteSpace(c)) c = "";
        if (!string.IsNullOrWhiteSpace(reqNumber) && c.IndexOf(reqTag, System.StringComparison.OrdinalIgnoreCase) < 0)
        {
            c = string.IsNullOrWhiteSpace(c) ? reqTag : (c + "\n" + reqTag);
        }
        if (!string.IsNullOrWhiteSpace(c)) p.Rows[0]["sComment"] = c;

        p.Insert();
        finalAssetId = (int)L(p.Rows[0]["ID"]);
    }
    else
    {
        if (chosenPortfolioId <= 0)
            throw new Exception("Для режима existing выберите chosenPortfolioId.");

        var p = new itamDataObject("itamPortfolio", "[ID] = " + chosenPortfolioId);
        if (p.Rows == null || p.Rows.Count == 0)
            throw new Exception("Карточка актива не найдена: ID=" + chosenPortfolioId);

        var row = p.Rows[0];
        if (updateUser) row["lUserId"] = (int)lUserId;
        if (updateLocation && lLocationId > 0)
        {
            if (row.ContainsKey("lt_lLocationId")) row["lt_lLocationId"] = (int)lLocationId;
            else if (row.ContainsKey("lLocationId")) row["lLocationId"] = (int)lLocationId;
            else row["lt_lLocationId"] = (int)lLocationId;
        }

        string oldComment = row.ContainsKey("sComment") && row["sComment"] != null ? row["sComment"].ToString() : "";
        if (!string.IsNullOrWhiteSpace(assetComment))
        {
            if (string.IsNullOrWhiteSpace(oldComment)) oldComment = assetComment;
            else oldComment = oldComment + "\n" + assetComment;
        }
        if (!string.IsNullOrWhiteSpace(reqNumber) &&
            oldComment.IndexOf(reqTag, System.StringComparison.OrdinalIgnoreCase) < 0)
        {
            oldComment = string.IsNullOrWhiteSpace(oldComment) ? reqTag : (oldComment + "\n" + reqTag);
        }
        row["sComment"] = oldComment;
        p.Update();
        finalAssetId = (int)chosenPortfolioId;
    }

    req["seStatus"] = 9;
    reqObj.Update();
    if (wizard.context != null)
    {
        wizard.context["finalAssetId"] = finalAssetId;
        wizard.context["processedRequestId"] = requestId;
        wizard.context["processedReqNumber"] = reqNumber;
    }
}
catch (System.Exception ex)
{
    throw new Exception(ex.Message);
}
