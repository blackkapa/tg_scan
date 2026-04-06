// Сервис A-Tracker ID1 — активы пользователя по ФИО (GET, параметр fio).
// Версия из вашей базы + поле sInventNumber по artibut_model (itamPortfolio).
// Веб и atracker_client читают инвентарный номер в первую очередь из sInventNumber.

try
{
    if (args.ContainsKey("fio"))
    {
        var fio = args["fio"];

        // грузим активы с указанным ФИО
        var assets = new itamDataObject(
            "itamPortfolio",
            fields: new System.Collections.Generic.List<string>()
            {
                "ID",
                "sFullName",            // наименование актива
                "sSerialNo",            // серийный номер
                "sInventNumber",        // инвентарный номер (каноническое имя в модели)
                "sInventoryNo",         // как у вас в базе; веб читает оба через API
                "lUserId.sFullName",    // ФИО пользователя
                "bInventoried",         // флаг «Проинвентаризирован»
                "dtInvent"              // дата инвентаризации
            },
            where: "[lUserId.sFullName] = @P1",
            parameters: new System.Collections.Generic.Dictionary<string, object>()
            {
                { "@P1", fio }
            }
        );

        data = assets.Rows;
        returnCode = "Success";
        message = "";
    }
    else
    {
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
        returnCode = "Error";
        message = "Не передан параметр fio";
    }
}
catch (System.Exception ex)
{
    data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    returnCode = "Error";
    message = ex.ToString();
}
