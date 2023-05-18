using System;
using System.Collections.Generic;

namespace upload_function.DB;

public partial class Division {
    public int Id { get; set; }

    public string Division1 { get; set; }

    public virtual ICollection<Document> Documents { get; set; } = new List<Document>();
}
