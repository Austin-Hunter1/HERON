// HERON receive-only worker. One process owns one USRP and its selected channels.
#include <uhd/usrp/multi_usrp.hpp>
#include <uhd/types/stream_cmd.hpp>
#include <uhd/types/tune_request.hpp>
#include <uhd/device.hpp>
#include <boost/property_tree/ptree.hpp>
#include <boost/property_tree/json_parser.hpp>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <complex>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <memory>
#include <cctype>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
namespace fs=std::filesystem;
namespace pt=boost::property_tree;
using Clock=std::chrono::steady_clock;
using Sample=std::complex<int16_t>;
std::string quote(const std::string& s) {
    std::ostringstream out; out << '"';
    for(unsigned char c:s) {if(c=='"'||c=='\\')out<<'\\'<<c;else if(c=='\n')out<<"\\n";else if(c=='\r')out<<"\\r";else if(c<32)out<<"?";else out<<c;}
    out<<'"';return out.str();
}
void emit(const std::string& s){std::cout<<s<<std::endl;}
void sync_file(const fs::path& p) {
    int fd=::open(p.c_str(),O_RDONLY);
    if(fd<0)throw std::runtime_error("Cannot reopen recording for sync: "+p.string());
    int result=fsync(fd);::close(fd);
    if(result)throw std::runtime_error("Disk sync failed: "+p.string());
}
struct Frame {std::vector<std::vector<Sample>> channels;double time;int error;};
class Writer {
    std::mutex mutex;
    std::condition_variable cv;
    std::deque<Frame> queue;
    std::thread thread;
    bool stopping=false;
    std::string failure;
    fs::path directory;
    bool eight;
    double scale8;
    double rate;
    uint64_t segment_samples;
    size_t count;
    void run() {
      try {
        uint64_t offset=0,segment=0,in_segment=0;
        std::vector<std::ofstream> files(count);
        std::vector<fs::path> paths(count);
        std::ofstream meta(directory/"samples.jsonl");
        if(!meta)throw std::runtime_error("Cannot open sample metadata");
        meta<<std::setprecision(17);
        auto close_files=[&](){for(size_t c=0;c<count;c++){if(files[c].is_open()){files[c].flush();files[c].close();if(files[c].fail())throw std::runtime_error("Disk write/close failed");sync_file(paths[c]);}}};
        auto open_files=[&](){for(size_t c=0;c<count;c++){std::ostringstream n;n<<"channel_"<<c<<"_"<<std::setw(6)<<std::setfill('0')<<segment<<(eight?".sc8":".sc16");paths[c]=directory/n.str();files[c].clear();files[c].open(paths[c],std::ios::binary|std::ios::out);if(!files[c])throw std::runtime_error("Cannot open sample file");}};
        open_files();
        for(;;){
          Frame f;
          {std::unique_lock<std::mutex> lock(mutex);cv.wait(lock,[&]{return stopping||!queue.empty();});if(queue.empty()&&stopping)break;f=std::move(queue.front());queue.pop_front();}
          size_t consumed=0,n=f.channels[0].size();
          while(consumed<n){
            if(in_segment==segment_samples){close_files();segment++;in_segment=0;open_files();}
            size_t take=std::min<uint64_t>(n-consumed,segment_samples-in_segment);
            for(size_t c=0;c<count;c++){
              if(eight){std::vector<int8_t> bytes(take*2);uint64_t clip=0;
                auto q=[&](int16_t v)->int8_t{long r=std::lround(v*scale8);if(r>127){r=127;clip++;}else if(r<-128){r=-128;clip++;}return int8_t(r);};
                for(size_t i=0;i<take;i++){bytes[2*i]=q(f.channels[c][consumed+i].real());bytes[2*i+1]=q(f.channels[c][consumed+i].imag());}
                clipped+=clip;files[c].write(reinterpret_cast<const char*>(bytes.data()),bytes.size());}
              else {std::vector<int16_t> bytes(take*2);for(size_t i=0;i<take;i++){bytes[2*i]=f.channels[c][consumed+i].real();bytes[2*i+1]=f.channels[c][consumed+i].imag();}files[c].write(reinterpret_cast<const char*>(bytes.data()),bytes.size()*sizeof(int16_t));}
              if(!files[c])throw std::runtime_error("Sample write failed (disk full or I/O error)");
            }
            meta<<"{\"sample_offset\":"<<offset<<",\"count\":"<<take<<",\"device_time\":"<<f.time+consumed/rate<<",\"segment\":"<<segment<<",\"segment_offset\":"<<in_segment<<",\"uhd_error\":"<<f.error<<"}\n";
            if(!meta)throw std::runtime_error("Metadata write failed");
            written+=take*count*(eight?2:4);consumed+=take;offset+=take;in_segment+=take;
          }
        }
        close_files();meta.flush();meta.close();if(meta.fail())throw std::runtime_error("Metadata close failed");sync_file(directory/"samples.jsonl");
      }catch(const std::exception& e){std::lock_guard<std::mutex> lock(mutex);failure=e.what();failed=true;}
    }
public:
    std::atomic<uint64_t> written{0};
    std::atomic<uint64_t> clipped{0};
    std::atomic<bool> failed{false};
    // sc8: scale so that `peak` x full-scale sc16 maps to +/-127 (UHD's own sc8 "peak" convention; SURGE used 0.25).
    Writer(const fs::path& p,bool sc8,double sr,int seconds,size_t ch,double peak=0.25):directory(p),eight(sc8),scale8(127.0/(32767.0*peak)),rate(sr),segment_samples(uint64_t(std::llround(sr*seconds))),count(ch){thread=std::thread(&Writer::run,this);}
    ~Writer(){try{finish();}catch(...) {}}
    void push(Frame&& f){std::lock_guard<std::mutex> lock(mutex);if(failed)throw std::runtime_error(failure);size_t maximum=std::max<size_t>(8,64*1024*1024/(32768*count*sizeof(Sample)));if(queue.size()>=maximum)throw std::runtime_error("Disk writer cannot keep up; receive queue exhausted");queue.push_back(std::move(f));cv.notify_one();}
    void finish(){if(thread.joinable()){{std::lock_guard<std::mutex> lock(mutex);stopping=true;cv.notify_one();}thread.join();}if(failed)throw std::runtime_error(failure);}
};

int main(int argc,char** argv){
 try {
    if(argc==2&&std::string(argv[1])=="--discover"){
        auto devices=uhd::device::find(uhd::device_addr_t("type=b200"));
        std::ostringstream out;out<<"{\"devices\":[";
        for(size_t i=0;i<devices.size();i++){if(i)out<<',';auto d=devices[i];std::string model=d.has_key("product")?d["product"]:"B2xx";std::transform(model.begin(),model.end(),model.begin(),::tolower);out<<"{\"serial\":"<<quote(d.has_key("serial")?d["serial"]:"")<<",\"model\":"<<quote(model)<<",\"name\":"<<quote(d.has_key("name")?d["name"]:model)<<"}";}
        out<<"]}";emit(out.str());return 0;
    }
    if(argc!=2)throw std::runtime_error("Usage: heron-recorder SETTINGS.json | --discover");
    pt::ptree cfg;pt::read_json(argv[1],cfg);
    const double rate=cfg.get<double>("sample_rate_msps")*1e6;
    const double frequency=cfg.get<double>("frequency_mhz")*1e6;
    const double bandwidth=cfg.get<double>("bandwidth_mhz")*1e6;
    const bool external=cfg.get<std::string>("clock")=="external",eight=cfg.get<std::string>("file_format")=="sc8";
    auto usrp=uhd::usrp::multi_usrp::make("type=b200,serial="+cfg.get<std::string>("serial"));
    const auto hardware_name=usrp->get_mboard_name();
    if(cfg.get<std::string>("model")=="b210"&&usrp->get_rx_num_channels()<2)throw std::runtime_error("Expected a two-channel B210");
    if(cfg.get<std::string>("model")=="b200"&&usrp->get_rx_num_channels()!=1)throw std::runtime_error("Radio model differs from the selected B200 profile");
    usrp->set_clock_source(external?"external":"internal");
    usrp->set_time_source(external?"external":"internal");
    std::vector<size_t> channels;
    pt::ptree actual;
    for(auto& entry:cfg.get_child("channels")){
        auto& ch=entry.second;if(!ch.get<bool>("enabled"))continue;
        size_t index=ch.get<size_t>("index");channels.push_back(index);
        usrp->set_rx_rate(rate,index);uhd::tune_request_t tune(frequency);if(cfg.get<bool>("int_n",true))tune.args=uhd::device_addr_t("mode_n=integer");usrp->set_rx_freq(tune,index);
        usrp->set_rx_gain(ch.get<double>("gain_db"),index);usrp->set_rx_bandwidth(bandwidth,index);usrp->set_rx_antenna(ch.get<std::string>("port"),index);
        if(std::abs(usrp->get_rx_rate(index)-rate)>1)throw std::runtime_error("Requested sample rate was not achieved");
        pt::ptree a;a.put("physical_channel",index);a.put("label",ch.get<std::string>("label"));a.put("gain_db",usrp->get_rx_gain(index));a.put("sample_rate_hz",usrp->get_rx_rate(index));a.put("frequency_hz",usrp->get_rx_freq(index));a.put("bandwidth_hz",usrp->get_rx_bandwidth(index));a.put("port",usrp->get_rx_antenna(index));actual.push_back(std::make_pair("",a));
    }
    if(channels.empty())throw std::runtime_error("No enabled receive channels");
    auto sensor_names=usrp->get_mboard_sensor_names();
    bool has_ref=std::find(sensor_names.begin(),sensor_names.end(),"ref_locked")!=sensor_names.end();
    if(external&&!has_ref)throw std::runtime_error("This UHD/device combination cannot verify the external reference lock");
    auto until=Clock::now()+std::chrono::seconds(10);
    bool locked=false;
    do {
        locked=!external||usrp->get_mboard_sensor("ref_locked").to_bool();
        for(auto c:channels){auto sensors=usrp->get_rx_sensor_names(c);if(std::find(sensors.begin(),sensors.end(),"lo_locked")!=sensors.end())locked=locked&&usrp->get_rx_sensor("lo_locked",c).to_bool();}
        if(!locked)std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }while(!locked&&Clock::now()<until);
    if(!locked)throw std::runtime_error("Radio reference/LO did not lock");
    if(!external)usrp->set_time_now(uhd::time_spec_t(0.0));
    emit("{\"event\":\"configured\"}");
    uhd::stream_args_t args("sc16","sc16");args.channels=channels;
    auto stream=usrp->get_rx_stream(args);
    constexpr size_t buffer_samples=32768;
    std::vector<std::vector<Sample>> buffers(channels.size(),std::vector<Sample>(buffer_samples));
    std::vector<void*> pointers;for(auto& b:buffers)pointers.push_back(b.data());
    bool streaming=false,recording=false,pending=false,quit=false,synced=!external;
    std::unique_ptr<Writer> writer;
    std::string pending_path;
    double target=-1,expected=-1;
    uint64_t overflows=0,gaps=0,last_bytes=0,final_bytes=0;
    auto last_status=Clock::now(),last_rx=Clock::now();
    auto stop_record=[&](){pending=false;recording=false;if(writer){writer->finish();final_bytes=writer->written.load();writer.reset();}emit("{\"event\":\"stopped\",\"state\":\"READY\",\"bytes_written\":"+std::to_string(final_bytes)+",\"rate_mbps\":0,\"overflows\":"+std::to_string(overflows)+",\"gaps\":"+std::to_string(gaps)+"}");};
    while(!quit){
        pollfd input{STDIN_FILENO,POLLIN,0};int available=poll(&input,1,0);
        if(available>0&&(input.revents&(POLLIN|POLLHUP))){
            std::string command;if(!std::getline(std::cin,command)){quit=true;break;}
            if(command=="PHASE"){double age=usrp->get_time_now().get_real_secs()-usrp->get_time_last_pps().get_real_secs();emit("{\"event\":\"phase\",\"age\":"+std::to_string(age)+"}");}
            else if(command=="SYNC"){
                if(streaming)throw std::runtime_error("Cannot reset device time while streaming");
                double age=usrp->get_time_now().get_real_secs()-usrp->get_time_last_pps().get_real_secs();
                if(age<0||age>.4)throw std::runtime_error("Missed synchronization window; prepare again");
                auto prior=usrp->get_time_last_pps();usrp->set_time_next_pps(uhd::time_spec_t(0.0));
                auto deadline=Clock::now()+std::chrono::seconds(2);
                while(usrp->get_time_last_pps()==prior&&Clock::now()<deadline)std::this_thread::sleep_for(std::chrono::milliseconds(5));
                if(std::abs(usrp->get_time_last_pps().get_real_secs())>.001)throw std::runtime_error("PPS time reset was not confirmed");
                synced=true;emit("{\"event\":\"synced\"}");
            }else if(command=="STREAM"){
                if(!synced)throw std::runtime_error("Synchronize external timing first");
                uhd::stream_cmd_t cmd(uhd::stream_cmd_t::STREAM_MODE_START_CONTINUOUS);cmd.stream_now=false;cmd.time_spec=usrp->get_time_now()+uhd::time_spec_t(.2);stream->issue_stream_cmd(cmd);streaming=true;last_rx=Clock::now();emit("{\"event\":\"ready\"}");
            }else if(command=="TIME")emit("{\"event\":\"time\",\"time\":"+std::to_string(usrp->get_time_now().get_real_secs())+"}");
            else if(command.rfind("START ",0)==0){
                if(!streaming||recording||pending)throw std::runtime_error("Recorder is not ready for START");
                std::istringstream s(command.substr(6));pt::ptree start;pt::read_json(s,start);pending_path=start.get<std::string>("path");target=start.get<double>("time");
                if(target>=0&&target<=usrp->get_time_now().get_real_secs())throw std::runtime_error("Scheduled recording time has already passed");
                fs::create_directories(pending_path);pt::ptree metadata=cfg;metadata.add_child("actual_channels",actual);metadata.put("device_model",hardware_name);metadata.put("sc8_conversion","round(sc16 * 127 / (32767 * sc8_peak)), clamped to int8; interleaved I,Q");metadata.put("sc8_peak",cfg.get<double>("sc8_peak",0.25));metadata.put("endianness","little");pt::write_json((fs::path(pending_path)/"settings.json").string(),metadata);
                writer=std::make_unique<Writer>(pending_path,eight,rate,cfg.get<int>("segment_seconds"),channels.size(),cfg.get<double>("sc8_peak",0.25));pending=true;expected=-1;overflows=gaps=last_bytes=final_bytes=0;
            }else if(command=="STOP")stop_record();
            else if(command=="QUIT"){stop_record();quit=true;}
            else throw std::runtime_error("Unknown recorder command");
        }
        if(quit)break;
        if(streaming){
            uhd::rx_metadata_t md;size_t n=stream->recv(pointers,buffer_samples,md,.1,false);
            if(md.error_code==uhd::rx_metadata_t::ERROR_CODE_OVERFLOW){overflows++;gaps++;}
            else if(md.error_code!=uhd::rx_metadata_t::ERROR_CODE_NONE&&md.error_code!=uhd::rx_metadata_t::ERROR_CODE_TIMEOUT)throw std::runtime_error("UHD receive error: "+md.strerror());
            if(n){last_rx=Clock::now();double t=md.time_spec.get_real_secs();size_t offset=0;
                if(pending&&(target<0||t+n/rate>target)){
                    if(target>=0){if(t-target>1.5/rate)throw std::runtime_error("First requested recording samples were missed");offset=size_t(std::max(0.0,std::ceil((target-t)*rate-1e-5)));}
                    pending=false;recording=true;emit("{\"event\":\"recording\"}");
                }
                if(recording&&offset<n){double first=t+offset/rate;if(expected>=0&&std::abs(first-expected)>1.5/rate)gaps++;expected=t+n/rate;Frame f;f.time=first;f.error=int(md.error_code);for(auto& b:buffers)f.channels.emplace_back(b.begin()+offset,b.begin()+n);writer->push(std::move(f));}
            }
            if(Clock::now()-last_rx>std::chrono::seconds(3))throw std::runtime_error("No samples received for three seconds");
        }else std::this_thread::sleep_for(std::chrono::milliseconds(10));
        if(writer&&writer->failed)throw std::runtime_error("Asynchronous disk write failed");
        auto now=Clock::now();double elapsed=std::chrono::duration<double>(now-last_status).count();
        if(elapsed>=1){
            if(external&&!usrp->get_mboard_sensor("ref_locked").to_bool())throw std::runtime_error("External reference lock lost");
            uint64_t bytes=writer?writer->written.load():final_bytes;double mbps=bytes>=last_bytes?(bytes-last_bytes)/elapsed/1e6:0;last_bytes=bytes;last_status=now;
            std::ostringstream s;s<<"{\"event\":\"status\",\"state\":"<<quote(recording?"RECORDING":pending?"STARTING":streaming?"READY":"PREPARING")<<",\"bytes_written\":"<<bytes<<",\"rate_mbps\":"<<(recording?mbps:0)<<",\"overflows\":"<<overflows<<",\"gaps\":"<<gaps<<",\"clipped\":"<<(writer?writer->clipped.load():0)<<",\"reference\":"<<quote(external?"10 MHz locked / PPS aligned":"internal / independent")<<"}";emit(s.str());
        }
    }
    if(writer)writer->finish();
    if(streaming){uhd::stream_cmd_t stop(uhd::stream_cmd_t::STREAM_MODE_STOP_CONTINUOUS);stream->issue_stream_cmd(stop);}
    return 0;
 }catch(const std::exception& e){emit("{\"event\":\"error\",\"message\":"+quote(e.what())+"}");std::cerr<<e.what()<<std::endl;return 1;}
}
