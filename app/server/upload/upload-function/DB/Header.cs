using System;
using System.Collections.Generic;

namespace upload_function.DB;

public partial class Header {
    public int Id { get; set; }

    public int AttachmentId { get; set; }

    public string Header1 { get; set; }
}
