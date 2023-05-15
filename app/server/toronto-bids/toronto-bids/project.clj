(defproject toronto-bids "0.1.0-SNAPSHOT"
  :description "Server for Toronto bids"
  :url "https://github.com/CivicTechTO/toronto-bids#readme"
  :license {:name "EPL-2.0 OR GPL-2.0-or-later WITH Classpath-exception-2.0"
            :url "https://www.eclipse.org/legal/epl-2.0/"}
  :dependencies 
	  [
		  	[org.clojure/clojure "1.10.0"]
		   [ring/ring-core "1.9.6"]
					[ring/ring-jetty-adapter "1.9.6"]
		   [ring-cors "0.1.13"]
		   [ring/ring-json "0.5.1"]
					[compojure "1.7.0"]
					[org.clojure/data.json "2.4.0"]
					[org.clojure/java.jdbc "0.7.12"]
					[mysql/mysql-connector-java "8.0.32"]
					[bananaoomarang/ring-debug-logging "1.1.0"]
	  ]
  :main ^:skip-aot toronto-bids.core
  :target-path "target/%s"
  :profiles {:uberjar {:aot :all :uberjar-name "toronto-bids.jar"}}
  )
