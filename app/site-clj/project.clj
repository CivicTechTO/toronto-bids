(defproject frontend "0.1.0-SNAPSHOT"
	:description "Test front end for Toronto Bids project"
	:dependencies 
		[
			[org.clojure/clojure "1.10.0"]
			[ring/ring-core "1.9.6"]
			[ring/ring-jetty-adapter "1.9.6"]
			[compojure "1.7.0"]
			[org.clojure/data.json "2.4.0"]
			[hiccup "1.0.5"]
			[clj-http "3.12.3"]
			[bananaoomarang/ring-debug-logging "1.1.0"]
		]
	:main ^:skip-aot frontend.core
	:target-path "target/%s"
	:profiles {:uberjar {:aot :all :uberjar-name "frontend.jar"}}
)
