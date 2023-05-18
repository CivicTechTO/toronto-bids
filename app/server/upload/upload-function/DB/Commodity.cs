using System;
using System.Collections.Generic;

namespace upload_function.DB;

public partial class Commodity {
    public int Id { get; set; }

    public string CommodityName { get; set; }

    public virtual ICollection<CommoditySubType> CommodityTypes { get; set; } = new List<CommoditySubType>();
}
