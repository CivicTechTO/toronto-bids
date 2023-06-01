using System;
using System.Collections.Generic;
using Azure.Security.KeyVault.Secrets;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata;
using Azure.Identity;
using Azure.Security.KeyVault.Secrets;

namespace upload_function.DB.Flat {
    public partial class FlatDBContext : DbContext {
        public FlatDBContext() {
        }

        public FlatDBContext(DbContextOptions<FlatDBContext> options)
            : base(options) {
        }

        public virtual DbSet<Attachment> Attachments { get; set; }
        public virtual DbSet<Attachments2> Attachments2s { get; set; }
        public virtual DbSet<Attachments3> Attachments3s { get; set; }
        public virtual DbSet<AttachmentsOld> AttachmentsOlds { get; set; }
        public virtual DbSet<AttachmentsPlay> AttachmentsPlays { get; set; }
        public virtual DbSet<Call> Calls { get; set; }
        public virtual DbSet<Calls2> Calls2s { get; set; }
        public virtual DbSet<Calls3> Calls3s { get; set; }
        public virtual DbSet<Fromxml> Fromxmls { get; set; }
        public virtual DbSet<FromxmlBackup> FromxmlBackups { get; set; }

        protected override void OnConfiguring(DbContextOptionsBuilder optionsBuilder)
        {
            if (!optionsBuilder.IsConfigured)
            {
                const string secretName = "DBCONNECTIONSTRINGUPLOADER";
                var keyVaultName = "obt-keys";
                var kvUri = $"https://{keyVaultName}.vault.azure.net";

                var client = new SecretClient(new Uri(kvUri), new DefaultAzureCredential());

                var secret = client.GetSecret(secretName);
                optionsBuilder.UseMySql(secret.Value.Value, ServerVersion.Parse("5.7.23-mysql"));
            }
        }

        protected override void OnModelCreating(ModelBuilder modelBuilder) {
            modelBuilder.UseCollation("utf8_unicode_ci")
                .HasCharSet("utf8");

            modelBuilder.Entity<Attachment>(entity =>
            {
                entity.HasKey(e => new { e.CallNumber, e.Filename })
                    .HasName("PRIMARY")
                    .HasAnnotation("MySql:IndexPrefixLength", new[] { 0, 0 });

                entity.ToTable("attachments");

                entity.HasIndex(e => e.CallNumber, "CallNumber");

                entity.HasIndex(e => e.Parsedtext, "parsedtext")
                    .HasAnnotation("MySql:FullTextIndex", true);

                entity.Property(e => e.CallNumber).HasMaxLength(30);

                entity.Property(e => e.Filename)
                    .HasMaxLength(256)
                    .HasColumnName("filename");

                entity.Property(e => e.Lastupdated)
                    .HasColumnType("datetime")
                    .HasColumnName("lastupdated")
                    .HasDefaultValueSql("CURRENT_TIMESTAMP");

                entity.Property(e => e.Parsedtext)
                    .HasColumnType("longtext")
                    .HasColumnName("parsedtext");

                entity.Property(e => e.Uuid)
                    .IsRequired()
                    .HasMaxLength(36)
                    .HasColumnName("uuid");
            });

            modelBuilder.Entity<Attachments2>(entity =>
            {
                entity.HasKey(e => e.Uuid)
                    .HasName("PRIMARY");

                entity.ToTable("attachments2");

                entity.HasIndex(e => e.CallNumber, "CallNumber");

                entity.HasIndex(e => e.Parsedtext, "parsedtext")
                    .HasAnnotation("MySql:FullTextIndex", true);

                entity.Property(e => e.Uuid)
                    .HasMaxLength(36)
                    .HasColumnName("uuid");

                entity.Property(e => e.CallNumber)
                    .IsRequired()
                    .HasMaxLength(30);

                entity.Property(e => e.Filename)
                    .IsRequired()
                    .HasMaxLength(256)
                    .HasColumnName("filename");

                entity.Property(e => e.Parsedtext)
                    .HasColumnType("longtext")
                    .HasColumnName("parsedtext");
            });

            modelBuilder.Entity<Attachments3>(entity =>
            {
                entity.HasKey(e => e.Uuid)
                    .HasName("PRIMARY");

                entity.ToTable("attachments3");

                entity.HasIndex(e => e.CallNumber, "CallNumber");

                entity.HasIndex(e => e.Parsedtext, "parsedtext")
                    .HasAnnotation("MySql:FullTextIndex", true);

                entity.Property(e => e.Uuid)
                    .HasMaxLength(36)
                    .HasColumnName("uuid");

                entity.Property(e => e.CallNumber)
                    .IsRequired()
                    .HasMaxLength(30);

                entity.Property(e => e.Filename)
                    .IsRequired()
                    .HasMaxLength(256)
                    .HasColumnName("filename");

                entity.Property(e => e.Parsedtext)
                    .HasColumnType("longtext")
                    .HasColumnName("parsedtext");
            });

            modelBuilder.Entity<AttachmentsOld>(entity =>
            {
                entity.HasKey(e => e.Uuid)
                    .HasName("PRIMARY");

                entity.ToTable("attachments-old");

                entity.HasIndex(e => e.CallNumber, "CallNumber");

                entity.HasIndex(e => e.Parsedtext, "parsedtext")
                    .HasAnnotation("MySql:FullTextIndex", true);

                entity.Property(e => e.Uuid)
                    .HasMaxLength(36)
                    .HasColumnName("uuid");

                entity.Property(e => e.CallNumber)
                    .IsRequired()
                    .HasMaxLength(30);

                entity.Property(e => e.Filename)
                    .IsRequired()
                    .HasMaxLength(256)
                    .HasColumnName("filename");

                entity.Property(e => e.Lastupdated)
                    .HasColumnType("datetime")
                    .HasColumnName("lastupdated")
                    .HasDefaultValueSql("CURRENT_TIMESTAMP");

                entity.Property(e => e.Parsedtext)
                    .HasColumnType("longtext")
                    .HasColumnName("parsedtext");
            });

            modelBuilder.Entity<AttachmentsPlay>(entity =>
            {
                entity.HasKey(e => new { e.CallNumber, e.Filename })
                    .HasName("PRIMARY")
                    .HasAnnotation("MySql:IndexPrefixLength", new[] { 0, 0 });

                entity.ToTable("attachments-play");

                entity.HasIndex(e => e.CallNumber, "CallNumber");

                entity.HasIndex(e => e.Parsedtext, "parsedtext")
                    .HasAnnotation("MySql:FullTextIndex", true);

                entity.Property(e => e.CallNumber).HasMaxLength(30);

                entity.Property(e => e.Filename)
                    .HasMaxLength(256)
                    .HasColumnName("filename");

                entity.Property(e => e.Parsedtext)
                    .HasColumnType("longtext")
                    .HasColumnName("parsedtext");

                entity.Property(e => e.Uuid)
                    .IsRequired()
                    .HasMaxLength(36)
                    .HasColumnName("uuid");
            });

            modelBuilder.Entity<Call>(entity =>
            {
                entity.HasKey(e => e.CallNumber)
                    .HasName("PRIMARY");

                entity.ToTable("calls");

                entity.HasIndex(e => e.CallNumber, "CallNumber");

                entity.HasIndex(e => e.Description, "Description")
                    .HasAnnotation("MySql:FullTextIndex", true);

                entity.HasIndex(e => e.ShortDescription, "ShortDescription");

                entity.HasIndex(e => e.ShowBuyerNameList, "ShowBuyerNameList");

                entity.HasIndex(e => e.Parsedtext, "parsedtext")
                    .HasAnnotation("MySql:FullTextIndex", true);

                entity.Property(e => e.CallNumber).HasMaxLength(30);

                entity.Property(e => e.BuyerEmailShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.BuyerLocationShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.BuyerPhoneShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Commodity)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.CommodityType)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Description)
                    .IsRequired()
                    .HasMaxLength(10000);

                entity.Property(e => e.Division)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Lastupdated)
                    .HasColumnType("datetime")
                    .HasColumnName("lastupdated")
                    .HasDefaultValueSql("CURRENT_TIMESTAMP");

                entity.Property(e => e.Parsedtext)
                    .HasColumnType("longtext")
                    .HasColumnName("parsedtext");

                entity.Property(e => e.ShortDescription)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.ShowBuyerNameList)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.SiteMeeting)
                    .IsRequired()
                    .HasMaxLength(1000);

                entity.Property(e => e.Type)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Uuid)
                    .IsRequired()
                    .HasMaxLength(36)
                    .HasColumnName("uuid");
            });

            modelBuilder.Entity<Calls2>(entity =>
            {
                entity.HasKey(e => e.CallNumber)
                    .HasName("PRIMARY");

                entity.ToTable("calls2");

                entity.HasIndex(e => e.CallNumber, "CallNumber");

                entity.HasIndex(e => e.Description, "Description")
                    .HasAnnotation("MySql:FullTextIndex", true);

                entity.HasIndex(e => e.ShortDescription, "ShortDescription");

                entity.HasIndex(e => e.ShowBuyerNameList, "ShowBuyerNameList");

                entity.HasIndex(e => e.Parsedtext, "parsedtext")
                    .HasAnnotation("MySql:FullTextIndex", true);

                entity.Property(e => e.CallNumber).HasMaxLength(30);

                entity.Property(e => e.BuyerEmailShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.BuyerLocationShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.BuyerPhoneShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.ClosingDateOld).HasMaxLength(100);

                entity.Property(e => e.Commodity)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.CommodityType)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Description)
                    .IsRequired()
                    .HasMaxLength(10000);

                entity.Property(e => e.Division)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Lastupdated)
                    .HasColumnType("datetime")
                    .HasColumnName("lastupdated")
                    .HasDefaultValueSql("CURRENT_TIMESTAMP");

                entity.Property(e => e.Parsedtext)
                    .HasColumnType("longtext")
                    .HasColumnName("parsedtext");

                entity.Property(e => e.ShortDescription)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.ShowBuyerNameList)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.ShowDatePostedOld).HasMaxLength(30);

                entity.Property(e => e.SiteMeeting)
                    .IsRequired()
                    .HasMaxLength(1000);

                entity.Property(e => e.Type)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Urls)
                    .HasMaxLength(1000)
                    .HasColumnName("urls");

                entity.Property(e => e.Uuid)
                    .IsRequired()
                    .HasMaxLength(36)
                    .HasColumnName("uuid");
            });

            modelBuilder.Entity<Calls3>(entity =>
            {
                entity.HasKey(e => e.CallNumber)
                    .HasName("PRIMARY");

                entity.ToTable("calls3");

                entity.HasIndex(e => e.CallNumber, "CallNumber");

                entity.HasIndex(e => e.Description, "Description")
                    .HasAnnotation("MySql:FullTextIndex", true);

                entity.HasIndex(e => e.ShortDescription, "ShortDescription");

                entity.HasIndex(e => e.ShowBuyerNameList, "ShowBuyerNameList");

                entity.HasIndex(e => e.Parsedtext, "parsedtext")
                    .HasAnnotation("MySql:FullTextIndex", true);

                entity.Property(e => e.CallNumber).HasMaxLength(30);

                entity.Property(e => e.BuyerEmailShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.BuyerLocationShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.BuyerPhoneShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.ClosingDateOld).HasMaxLength(100);

                entity.Property(e => e.Commodity)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.CommodityType)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Description)
                    .IsRequired()
                    .HasMaxLength(10000);

                entity.Property(e => e.Division)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Lastupdated)
                    .HasColumnType("datetime")
                    .HasColumnName("lastupdated")
                    .HasDefaultValueSql("CURRENT_TIMESTAMP");

                entity.Property(e => e.Parsedtext)
                    .HasColumnType("longtext")
                    .HasColumnName("parsedtext");

                entity.Property(e => e.ShortDescription)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.ShowBuyerNameList)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.ShowDatePostedOld).HasMaxLength(30);

                entity.Property(e => e.SiteMeeting)
                    .IsRequired()
                    .HasMaxLength(1000);

                entity.Property(e => e.Type)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Urls)
                    .HasMaxLength(1000)
                    .HasColumnName("urls");

                entity.Property(e => e.Uuid)
                    .IsRequired()
                    .HasMaxLength(36)
                    .HasColumnName("uuid");
            });

            modelBuilder.Entity<Fromxml>(entity =>
            {
                entity.HasKey(e => e.Uuid)
                    .HasName("PRIMARY");

                entity.ToTable("fromxml");

                entity.HasIndex(e => e.CallNumber, "CallNumber");

                entity.Property(e => e.Uuid)
                    .HasMaxLength(36)
                    .HasColumnName("uuid");

                entity.Property(e => e.BuyerEmailShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.BuyerLocationShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.BuyerPhoneShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.CallNumber)
                    .IsRequired()
                    .HasMaxLength(30);

                entity.Property(e => e.ClosingDateOld)
                    .IsRequired()
                    .HasMaxLength(100);

                entity.Property(e => e.Commodity)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.CommodityType)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Description)
                    .IsRequired()
                    .HasMaxLength(10000);

                entity.Property(e => e.Division)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Lastupdated)
                    .HasColumnType("datetime")
                    .HasColumnName("lastupdated")
                    .HasDefaultValueSql("CURRENT_TIMESTAMP");

                entity.Property(e => e.Parsedtext)
                    .IsRequired()
                    .HasColumnName("parsedtext");

                entity.Property(e => e.ShortDescription)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.ShowBuyerNameList)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.ShowDatePostedOld)
                    .IsRequired()
                    .HasMaxLength(30);

                entity.Property(e => e.SiteMeeting)
                    .IsRequired()
                    .HasMaxLength(1000);

                entity.Property(e => e.Type)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Urls)
                    .IsRequired()
                    .HasMaxLength(1000)
                    .HasColumnName("urls");
            });

            modelBuilder.Entity<FromxmlBackup>(entity =>
            {
                entity.HasKey(e => e.Uuid)
                    .HasName("PRIMARY");

                entity.ToTable("fromxml-backup");

                entity.HasIndex(e => e.CallNumber, "CallNumber");

                entity.Property(e => e.Uuid)
                    .HasMaxLength(36)
                    .HasColumnName("uuid");

                entity.Property(e => e.BuyerEmailShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.BuyerLocationShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.BuyerPhoneShow)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.CallNumber)
                    .IsRequired()
                    .HasMaxLength(30);

                entity.Property(e => e.ClosingDate)
                    .IsRequired()
                    .HasMaxLength(100);

                entity.Property(e => e.Commodity)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.CommodityType)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Description)
                    .IsRequired()
                    .HasMaxLength(10000);

                entity.Property(e => e.Division)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Lastupdated)
                    .HasColumnType("datetime")
                    .HasColumnName("lastupdated")
                    .HasDefaultValueSql("CURRENT_TIMESTAMP");

                entity.Property(e => e.Parsedtext)
                    .IsRequired()
                    .HasColumnName("parsedtext");

                entity.Property(e => e.ShortDescription)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.ShowBuyerNameList)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.ShowDatePosted)
                    .IsRequired()
                    .HasMaxLength(30);

                entity.Property(e => e.SiteMeeting)
                    .IsRequired()
                    .HasMaxLength(1000);

                entity.Property(e => e.Type)
                    .IsRequired()
                    .HasMaxLength(256);

                entity.Property(e => e.Urls)
                    .IsRequired()
                    .HasMaxLength(1000)
                    .HasColumnName("urls");
            });

            OnModelCreatingPartial(modelBuilder);
        }

        partial void OnModelCreatingPartial(ModelBuilder modelBuilder);
    }
}
