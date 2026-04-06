// Сервис A-Tracker: список категорий активов (GET) для подстановки «Тип» в накладной.
// Скопируйте в карточку сервиса. В config.ini: categories_list_service_id = <ID>.
// Структура как у LocationsList: itamCategory, поля ID, sFullName, lParentId.

try
{
    var fields = new System.Collections.Generic.List<string> { "ID", "sFullName", "lParentId" };
    var cat = new itamDataObject(
        "itamCategory",
        fields: fields,
        where: "1=1",
        parameters: new System.Collections.Generic.Dictionary<string, object>());

    var list = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    if (cat.Rows != null)
    {
        foreach (var row in cat.Rows)
        {
            var d = new System.Collections.Generic.Dictionary<string, object>();
            if (row.ContainsKey("ID"))
                d["ID"] = row["ID"];
            string name = "";
            if (row.ContainsKey("sFullName") && row["sFullName"] != null)
                name = row["sFullName"].ToString();
            else if (row.ContainsKey("SFullName") && row["SFullName"] != null)
                name = row["SFullName"].ToString();
            d["sFullName"] = name;
            if (row.ContainsKey("lParentId") && row["lParentId"] != null)
                d["lParentId"] = row["lParentId"];
            list.Add(d);
        }
    }

    data = list;
    returnCode = "Success";
    message = "OK";
}
catch (System.Exception ex)
{
    returnCode = "Error";
    message = ex.ToString();
    data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
}
