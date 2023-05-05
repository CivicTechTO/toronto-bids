(defproject upload "0.1.0-SNAPSHOT"
  :description "Upload data for Toronto bids"
  :dependencies 
  [
  	[org.clojure/clojure "1.10.0"]
		[ring/ring-core "1.9.6"]
		[ring/ring-jetty-adapter "1.9.6"]
		[compojure "1.7.0"]
		[org.clojure/data.json "2.4.0"]
		[org.clojure/java.jdbc "0.7.12"]
		[mysql/mysql-connector-java "8.0.32"]
		[bananaoomarang/ring-debug-logging "1.1.0"]
	]
  :main ^:skip-aot upload.core
  :target-path "target/%s"
  :profiles {:uberjar {:aot :all :uberjar-name "upload-bids.jar"}}
)
