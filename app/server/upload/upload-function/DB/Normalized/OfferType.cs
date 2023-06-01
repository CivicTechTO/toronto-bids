using System;
using System.Collections.Generic;

namespace upload_function.DB.Normalized;

public partial class OfferType {
    public int Id { get; set; }

    public string Type1 { get; set; }

    public virtual ICollection<Document> Documents { get; set; } = new List<Document>();
}
